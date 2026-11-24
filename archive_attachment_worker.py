"""Experimental attachment cycle; not connected to the deployed worker."""
import json

from archive_file_job import FileCopyJob, _file_text_source, make_file_record
from archive_files import verify_attachment_coverage, verify_message_files
from archive_worker import ArchiveWorker, atomic_json
from backend import validate_id
from voice_archive import ArchiveError, verify_archive, visible_snapshot


class AttachmentArchiveWorker(ArchiveWorker):
    def __init__(self,root,cloud,write,closed_state,transfer):
        super().__init__(root,cloud,write,closed_state)
        self.files=FileCopyJob(self.root,transfer);self.transfer=transfer

    def _proof_path(self,name):
        if not isinstance(name,str):raise ArchiveError('Attachment proof missing')
        path=(self.root/name).resolve()
        if path.parent!=self.root.resolve():raise ArchiveError('Invalid attachment proof path')
        return path

    def _reconcile_delete(self,cid,project,job):
        try:
            proof=json.loads(self._proof_path(job['file_proof']).read_text(encoding='utf-8'))
            snapshot=json.loads(self._proof_path(job['snapshot']).read_text(encoding='utf-8'))
        except (OSError,ValueError,KeyError):raise ArchiveError('Attachment deletion proof unavailable') from None
        record=job.get('record',{});archive_id=job.get('archive_id')
        if (proof.get('source_id')!=cid or proof.get('project_id')!=project or proof.get('archive_id')!=archive_id
                or proof.get('record_sha256')!=record.get('sha256') or proof.get('message_id')!=job.get('archive_message_id')):
            raise ArchiveError('Attachment deletion proof mismatch')
        archive=self.cloud.read(archive_id)
        if not archive or verify_archive(record,archive,snapshot)!=proof['message_id']:
            raise ArchiveError('Archived transcript is not confirmed')
        verify_attachment_coverage(proof['inventory'],proof['copies'])
        verify_message_files(archive,proof['message_id'],proof['copies'],self.transfer)
        if cid in self.cloud.chats(project):raise ArchiveError('Source deletion is not confirmed')
        job['state']='deleted';self.save(cid,job)
        return 'deleted'

    def process(self,identity,now):
        cid=validate_id(identity.get('conversation_id'));project=validate_id(identity.get('gizmo_id'))
        job=self.job(cid)
        if job.get('state')=='deleted':return 'deleted'
        self._closed({'source_id':cid,'project_id':project})
        source=self.cloud.read(cid)
        if source is None:
            if job.get('state')!='deleting':raise ArchiveError('Source is missing without a verified deletion receipt')
            return self._reconcile_delete(cid,project,job)
        if job.get('state')=='deleting':raise ArchiveError('Previous deletion remains unconfirmed; no repeat')
        record=make_file_record(source,cid,project)
        if not job:
            snapshot='file-source-'+cid+'-'+record['sha256']+'.json'
            atomic_json(self.root/snapshot,visible_snapshot(_file_text_source(source,cid,project)))
            job={'kind':'attachments_v1','state':'pending','record':record,'snapshot':snapshot,'stable_since':now}
            self.save(cid,job);return 'pending'
        if job.get('kind')!='attachments_v1' or job.get('record')!=record:
            raise ArchiveError('Source or archive job changed; retain source')
        if job.get('state') not in ('pending','copied'):raise ArchiveError('Unknown attachment archive state')
        if job['state']=='pending' and now-job.get('stable_since',now)<30:return 'pending'
        archive_id=self.bindings.get(project)
        if not archive_id:raise ArchiveError('Reserved archive is missing; retain source')
        self.files.prepare(source,cid,project)
        message_id=self.files.send_or_reconcile(record,source,archive_id,cid,project,self.cloud.read,self.write,
            lambda:self._closed(record),self.cloud.read)
        job.update(state='copied',archive_id=archive_id,archive_message_id=message_id);self.save(cid,job)
        fresh=self.cloud.read(cid);archive=self.cloud.read(archive_id)
        if not fresh or not archive:raise ArchiveError('Source or archive is unavailable; retain source')
        if self.files.verify_full(record,fresh,archive,cid,project)!=message_id:
            raise ArchiveError('Archive message changed; retain source')
        self._closed(record)
        file_job=json.loads((self.root/('file-job-'+cid+'.json')).read_text(encoding='utf-8'))
        proof_name='file-proof-'+cid+'-'+record['sha256']+'.json'
        atomic_json(self.root/proof_name,{'source_id':cid,'project_id':project,'archive_id':archive_id,
            'message_id':message_id,'record_sha256':record['sha256'],
            'inventory':file_job['inventory'],'copies':file_job['copies']})
        job.update(state='deleting',file_proof=proof_name,verified_at=now);self.save(cid,job)
        self.cloud.delete(cid)
        if self.cloud.read(cid) is not None or cid in self.cloud.chats(project):
            raise ArchiveError('Deletion has not been confirmed')
        job.update(state='deleted',deleted_at=now);self.save(cid,job)
        return 'deleted'
