"""Staged file-copy preparation; never sends a chat message or deletes a source."""
import copy
import hashlib
import json
from pathlib import Path

from archive_files import collect_source_files, verify_attachment_coverage, verify_message_files, verify_source_files
from archive_worker import NotSubmitted, atomic_json
from backend import validate_id
from voice_archive import archive_message, make_record, verify_archive


class FileJobError(ValueError):
    pass


def _file_text_source(conversation,source_id,project_id):
    inventory=collect_source_files(conversation,source_id,project_id)
    if not inventory:raise FileJobError('No supported source attachments')
    result=copy.deepcopy(conversation)
    for entry in reversed(inventory):
        message=result['mapping'][entry['message_id']]['message']
        attachments=message['metadata']['attachments']
        attachment=attachments.pop(entry['index'])
        if attachment.get('id')!=entry['id']:raise FileJobError('Attachment identity changed')
        parts=message.get('content',{}).get('parts')
        if not isinstance(parts,list):raise FileJobError('Unsupported file message content')
        identity=hashlib.sha256(entry['id'].encode('utf-8')).hexdigest()
        marker=json.dumps({'name':entry['name'],'mime_type':entry['mime_type'],'size':entry['size'],
                           'index':entry['index'],'source_id_sha256':identity},ensure_ascii=False,sort_keys=True)
        parts.append('[Вложение: '+marker+']')
    return result


def make_file_record(conversation,source_id,project_id):
    return make_record(_file_text_source(conversation,source_id,project_id),source_id,project_id)


class FileCopyJob:
    def __init__(self,root,transfer):
        self.root=Path(root);self.transfer=transfer

    def prepare(self,conversation,source_id,project_id):
        source_id=validate_id(source_id);project_id=validate_id(project_id)
        inventory=collect_source_files(conversation,source_id,project_id)
        if not inventory:raise FileJobError('No supported source attachments')
        path=self.root/('file-job-'+source_id+'.json')
        self.root.mkdir(parents=True,exist_ok=True)
        if path.exists():
            job=json.loads(path.read_text(encoding='utf-8'))
            if (job.get('version')!=1 or job.get('source_id')!=source_id or job.get('project_id')!=project_id
                    or job.get('inventory')!=inventory):raise FileJobError('Source attachments changed; retain original')
            if job.get('state') not in ('pending','ready','copying'):raise FileJobError('Unknown file job state')
        else:
            job={'version':1,'source_id':source_id,'project_id':project_id,'inventory':inventory,'copies':[],'state':'pending'}
            atomic_json(path,job)
        copies=job.get('copies')
        if not isinstance(copies,list) or len(copies)>len(inventory):raise FileJobError('Invalid file job receipt')
        if copies:
            verify_attachment_coverage(inventory[:len(copies)],copies)
        if job['state']=='copying':
            reserved=job.get('reserved')
            if not reserved or len(copies)>=len(inventory):raise FileJobError('Upload outcome uncertain; reconcile before retry')
            entry=inventory[len(copies)]
            result=self.transfer.recover(entry,reserved)
            copies.append({**result,'message_id':entry['message_id'],'index':entry['index']})
            verify_attachment_coverage(inventory[:len(copies)],copies)
            job.pop('reserved',None);job['state']='pending';atomic_json(path,job)
        while len(copies)<len(inventory):
            entry=inventory[len(copies)]
            job['state']='copying';atomic_json(path,job)
            def remember(file_id):
                job['reserved']=validate_id(file_id);atomic_json(path,job)
            result=self.transfer.copy(entry,on_reserved=remember)
            copies.append({**result,'message_id':entry['message_id'],'index':entry['index']})
            verify_attachment_coverage(inventory[:len(copies)],copies)
            job.pop('reserved',None);job['state']='pending';atomic_json(path,job)
        verify_attachment_coverage(inventory,copies)
        job['state']='ready';atomic_json(path,job)
        return copies

    def verify(self,source,archive,message_id,source_id,project_id):
        source_id=validate_id(source_id);project_id=validate_id(project_id)
        if (not isinstance(archive,dict) or archive.get('gizmo_id')!=project_id
                or not archive.get('conversation_id') or archive.get('conversation_id')==source_id
                or archive.get('is_archived')):raise FileJobError('Invalid destination conversation')
        path=self.root/('file-job-'+source_id+'.json')
        try:job=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):raise FileJobError('File receipt is unavailable') from None
        inventory=collect_source_files(source,source_id,project_id)
        if (job.get('version')!=1 or job.get('state')!='ready' or job.get('source_id')!=source_id
                or job.get('project_id')!=project_id or job.get('inventory')!=inventory):
            raise FileJobError('Source files or receipt changed')
        copies=job.get('copies',[])
        verify_attachment_coverage(inventory,copies)
        verify_message_files(archive,message_id,copies,self.transfer)
        verify_source_files(copies,self.transfer)
        return True

    def verify_full(self,record,source,archive,source_id,project_id):
        normalized=_file_text_source(source,source_id,project_id)
        message_id=verify_archive(record,archive,normalized)
        self.verify(source,archive,message_id,source_id,project_id)
        return message_id

    def begin_submission(self,record,source,archive,source_id,project_id):
        source_id=validate_id(source_id);project_id=validate_id(project_id)
        archive_id=validate_id(archive.get('conversation_id'))
        if archive_id==source_id or archive.get('gizmo_id')!=project_id or archive.get('is_archived'):
            raise FileJobError('Invalid destination conversation')
        path=self.root/('file-job-'+source_id+'.json')
        try:job=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):raise FileJobError('File receipt is unavailable') from None
        inventory=collect_source_files(source,source_id,project_id)
        if (job.get('state')!='ready' or job.get('project_id')!=project_id or job.get('source_id')!=source_id
                or job.get('inventory')!=inventory or record!=make_file_record(source,source_id,project_id)):
            raise FileJobError('Source, record or file receipt changed')
        verify_attachment_coverage(inventory,job.get('copies',[]))
        if job.get('submission'):raise FileJobError('Submission already started; reconcile before retry')
        job['submission']={'state':'sending','archive_id':archive_id,'record_sha256':record['sha256']}
        atomic_json(path,job)

    def reconcile_submission(self,record,source,archive,source_id,project_id):
        source_id=validate_id(source_id);project_id=validate_id(project_id)
        path=self.root/('file-job-'+source_id+'.json')
        try:job=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):raise FileJobError('File receipt is unavailable') from None
        submission=job.get('submission') or {}
        if (submission.get('state') not in ('sending','confirmed') or submission.get('archive_id')!=archive.get('conversation_id')
                or submission.get('record_sha256')!=record.get('sha256')):
            raise FileJobError('Submission identity is not confirmed')
        message_id=self.verify_full(record,source,archive,source_id,project_id)
        if submission.get('state')=='confirmed' and submission.get('message_id')!=message_id:
            raise FileJobError('Archived message changed')
        submission.update(state='confirmed',message_id=message_id)
        atomic_json(path,job)
        return message_id

    def not_submitted(self,source_id,error):
        if not isinstance(error,NotSubmitted):raise FileJobError('Submission outcome is unknown')
        path=self.root/('file-job-'+validate_id(source_id)+'.json')
        try:job=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):raise FileJobError('File receipt is unavailable') from None
        if (job.get('source_id')!=source_id or (job.get('submission') or {}).get('state')!='sending'):
            raise FileJobError('No pending submission to reset')
        job.pop('submission');atomic_json(path,job)

    def send_or_reconcile(self,record,source,archive_id,source_id,project_id,read_archive,send,closed_check,read_source):
        source_id=validate_id(source_id);project_id=validate_id(project_id);archive_id=validate_id(archive_id)
        path=self.root/('file-job-'+source_id+'.json')
        try:job=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):raise FileJobError('File receipt is unavailable') from None
        closed_check()
        archive=read_archive(archive_id)
        if not isinstance(archive,dict):raise FileJobError('Destination archive is unavailable')
        fresh=read_source(source_id)
        if not isinstance(fresh,dict):raise FileJobError('Source conversation is unavailable')
        if job.get('submission'):
            return self.reconcile_submission(record,fresh,archive,source_id,project_id)
        if make_file_record(source,source_id,project_id)!=make_file_record(fresh,source_id,project_id):
            raise FileJobError('Source changed before submission')
        self.begin_submission(record,fresh,archive,source_id,project_id)
        try:
            actual=send(project_id,archive_id,archive_message([record]),job['copies'])
        except NotSubmitted as error:
            self.not_submitted(source_id,error)
            raise
        except Exception:
            closed_check()
            archive=read_archive(archive_id)
            fresh=read_source(source_id)
            if not isinstance(archive,dict) or not isinstance(fresh,dict):raise FileJobError('Submission outcome is unknown; retain source') from None
            return self.reconcile_submission(record,fresh,archive,source_id,project_id)
        if actual!=archive_id:raise FileJobError('Submission destination is uncertain; retain source')
        closed_check()
        archive=read_archive(archive_id)
        fresh=read_source(source_id)
        if not isinstance(archive,dict) or not isinstance(fresh,dict):raise FileJobError('Source or archive is unavailable; retain source')
        return self.reconcile_submission(record,fresh,archive,source_id,project_id)
