import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from archive_file_job import FileCopyJob, FileJobError, make_file_record
from voice_archive import archive_message
from archive_worker import NotSubmitted
from test_voice_archive import source,destination


class FileCopyJobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.doc=source()
        self.doc['mapping']['u']['message']['metadata']['attachments']=[{'id':'file_a','name':'a.pdf','mime_type':'application/pdf','size':3}]
        self.transfer=Mock()
        self.transfer.MAX_FILE_BYTES=512*1024*1024
        self.transfer.copy.return_value={'source_id':'file_a','id':'file_new','name':'a.pdf','mime_type':'application/pdf','size':3,'sha256':hashlib.sha256(b'abc').hexdigest()}
        self.job=FileCopyJob(self.root,self.transfer)

    def test_restarts_do_not_upload_a_second_copy(self):
        first=self.job.prepare(self.doc,'source','project')
        second=FileCopyJob(self.root,self.transfer).prepare(self.doc,'source','project')
        self.assertEqual(first,second)
        self.transfer.copy.assert_called_once()

    def test_unknown_upload_outcome_blocks_retry(self):
        self.transfer.copy.side_effect=TimeoutError('response lost')
        with self.assertRaises(TimeoutError):self.job.prepare(self.doc,'source','project')
        self.transfer.copy.reset_mock();self.transfer.copy.side_effect=None
        with self.assertRaises(FileJobError):FileCopyJob(self.root,self.transfer).prepare(self.doc,'source','project')
        self.transfer.copy.assert_not_called()

    def test_source_identity_or_attachment_change_does_not_reuse_copy(self):
        self.job.prepare(self.doc,'source','project')
        changed=copy.deepcopy(self.doc);changed['mapping']['u']['message']['metadata']['attachments'][0]['size']=4
        with self.assertRaises(FileJobError):self.job.prepare(changed,'source','project')
        self.transfer.copy.assert_called_once()

    def test_second_file_failure_preserves_first_receipt_and_blocks_unknown_upload(self):
        self.doc['mapping']['u']['message']['metadata']['attachments'].append({'id':'file_b','name':'b.txt','mime_type':'text/plain','size':4})
        self.transfer.copy.side_effect=[self.transfer.copy.return_value,TimeoutError('upload interrupted')]
        with self.assertRaises(TimeoutError):self.job.prepare(self.doc,'source','project')
        import json
        job=json.loads((self.root/'file-job-source.json').read_text(encoding='utf-8'))
        self.assertEqual(job['state'],'copying');self.assertEqual(len(job['copies']),1)
        self.transfer.copy.reset_mock();self.transfer.copy.side_effect=None
        with self.assertRaises(FileJobError):self.job.prepare(self.doc,'source','project')
        self.transfer.copy.assert_not_called()

    def test_verification_requires_project_message_and_current_source_bytes(self):
        copies=self.job.prepare(self.doc,'source','project')
        actual={k:copies[0][k] for k in ('id','name','mime_type','size')}
        archive={'conversation_id':'archive','gizmo_id':'project','current_node':'m','mapping':{
            'm':{'parent':None,'message':{'author':{'role':'user'},'status':'finished_successfully','metadata':{'attachments':[actual]}}}}}
        self.transfer._digest_file.return_value=hashlib.sha256(b'abc').hexdigest()
        self.assertTrue(self.job.verify(self.doc,archive,'m','source','project'))
        archive['gizmo_id']='other'
        with self.assertRaises(FileJobError):self.job.verify(self.doc,archive,'m','source','project')
        archive['gizmo_id']='project';self.transfer._digest_file.return_value='b'*64
        with self.assertRaises(ValueError):self.job.verify(self.doc,archive,'m','source','project')

    def test_interrupted_upload_recovers_known_copy_without_uploading_again(self):
        result=self.transfer.copy.return_value
        def interrupted(entry,on_reserved):
            on_reserved('file_new')
            raise TimeoutError('upload acknowledgement lost')
        self.transfer.copy.side_effect=interrupted
        with self.assertRaises(TimeoutError):self.job.prepare(self.doc,'source','project')
        self.transfer.copy.reset_mock();self.transfer.copy.side_effect=None
        self.transfer.recover.return_value=result
        copies=FileCopyJob(self.root,self.transfer).prepare(self.doc,'source','project')
        self.assertEqual(copies[0]['id'],'file_new')
        self.transfer.recover.assert_called_once()
        self.transfer.copy.assert_not_called()

    def test_missing_or_mismatched_reserved_upload_never_triggers_new_upload(self):
        def interrupted(entry,on_reserved):
            on_reserved('file_new')
            raise TimeoutError('uncertain upload')
        self.transfer.copy.side_effect=interrupted
        with self.assertRaises(TimeoutError):self.job.prepare(self.doc,'source','project')
        self.transfer.copy.reset_mock();self.transfer.recover.side_effect=ValueError('copy does not match')
        with self.assertRaises(ValueError):FileCopyJob(self.root,self.transfer).prepare(self.doc,'source','project')
        self.transfer.copy.assert_not_called()

    def test_full_proof_requires_exact_text_and_all_file_bytes(self):
        record=make_file_record(self.doc,'source','project')
        copies=self.job.prepare(self.doc,'source','project')
        archive=destination(archive_message([record]))
        archive['mapping']['copy']['message']['metadata']['attachments']=[{k:copies[0][k] for k in ('id','name','mime_type','size')}]
        self.transfer._digest_file.return_value=hashlib.sha256(b'abc').hexdigest()
        self.assertEqual(self.job.verify_full(record,self.doc,archive,'source','project'),'copy')
        changed=copy.deepcopy(self.doc);changed['mapping']['a']['message']['content']['parts'][0]+=' changed'
        with self.assertRaises(ValueError):self.job.verify_full(record,changed,archive,'source','project')
        missing=copy.deepcopy(archive);missing['mapping']['copy']['message']['metadata']['attachments']=[]
        with self.assertRaises(ValueError):self.job.verify_full(record,self.doc,missing,'source','project')
        self.transfer._digest_file.return_value='b'*64
        with self.assertRaises(ValueError):self.job.verify_full(record,self.doc,archive,'source','project')

    def test_record_keeps_text_image_marker_and_distinct_file_positions(self):
        m=self.doc['mapping']['u']['message']
        m['metadata']['attachments'].insert(0,{'id':'file_img','name':'x.jpg','mime_type':'image/jpeg','size':3})
        m['metadata']['attachments'].append({'id':'file_b','name':'b.txt','mime_type':'text/plain','size':4})
        m['content']['parts'].append({'content_type':'image_asset_pointer','asset_pointer':'sediment://file_img','mime_type':'image/jpeg','size_bytes':3})
        original=copy.deepcopy(self.doc)
        record=make_file_record(self.doc,'source','project')
        self.assertEqual(self.doc,original)
        parts=record['messages'][0]['parts']
        self.assertEqual(parts[0],'Все, еще?')
        self.assertEqual(sum('Изображение пропущено' in p for p in parts),1)
        self.assertEqual(sum(p.startswith('[Вложение: ') for p in parts),2)
        self.assertTrue(any('a.pdf' in p and '"index": 1' in p for p in parts))
        self.assertTrue(any('b.txt' in p and '"index": 2' in p for p in parts))

    def test_uncertain_attachment_submission_reconciles_before_any_second_send(self):
        record=make_file_record(self.doc,'source','project')
        copies=self.job.prepare(self.doc,'source','project')
        self.job.begin_submission(record,self.doc,destination('previous'),'source','project')
        with self.assertRaises(FileJobError):self.job.begin_submission(record,self.doc,destination('previous'),'source','project')
        archive=destination(archive_message([record]))
        archive['mapping']['copy']['message']['metadata']['attachments']=[{k:copies[0][k] for k in ('id','name','mime_type','size')}]
        self.transfer._digest_file.return_value=hashlib.sha256(b'abc').hexdigest()
        resumed=FileCopyJob(self.root,self.transfer)
        self.assertEqual(resumed.reconcile_submission(record,self.doc,archive,'source','project'),'copy')
        self.assertEqual(resumed.reconcile_submission(record,self.doc,archive,'source','project'),'copy')

    def test_unverified_submission_cannot_be_marked_complete_or_resent(self):
        record=make_file_record(self.doc,'source','project')
        self.job.prepare(self.doc,'source','project')
        self.job.begin_submission(record,self.doc,destination('previous'),'source','project')
        archive=destination('incorrect')
        with self.assertRaises(ValueError):self.job.reconcile_submission(record,self.doc,archive,'source','project')
        with self.assertRaises(FileJobError):self.job.begin_submission(record,self.doc,destination('previous'),'source','project')

    def test_retry_allowed_only_when_no_submission_is_confirmed(self):
        record=make_file_record(self.doc,'source','project');self.job.prepare(self.doc,'source','project')
        self.job.begin_submission(record,self.doc,destination('previous'),'source','project')
        with self.assertRaises(FileJobError):self.job.not_submitted('source',TimeoutError('unknown'))
        with self.assertRaises(FileJobError):self.job.begin_submission(record,self.doc,destination('previous'),'source','project')
        self.job.not_submitted('source',NotSubmitted('button never clicked'))
        self.job.begin_submission(record,self.doc,destination('previous'),'source','project')

    def test_send_recovers_after_response_loss_without_duplicate(self):
        record=make_file_record(self.doc,'source','project');copies=self.job.prepare(self.doc,'source','project')
        cloud={'archive':destination('previous')};self.transfer._digest_file.return_value=hashlib.sha256(b'abc').hexdigest();closed=Mock()
        def writer(project,cid,payload,files):
            archive=destination(payload)
            archive['mapping']['copy']['message']['metadata']['attachments']=[{k:files[0][k] for k in ('id','name','mime_type','size')}]
            cloud[cid]=archive
            raise TimeoutError('response lost after send')
        send=Mock(side_effect=writer)
        result=self.job.send_or_reconcile(record,self.doc,'archive','source','project',cloud.get,send,closed,lambda _:self.doc)
        self.assertEqual(result,'copy');send.assert_called_once()
        resumed=FileCopyJob(self.root,self.transfer)
        self.assertEqual(resumed.send_or_reconcile(record,self.doc,'archive','source','project',cloud.get,send,closed,lambda _:self.doc),'copy')
        send.assert_called_once()

    def test_unknown_send_with_no_copy_cannot_resend(self):
        record=make_file_record(self.doc,'source','project');self.job.prepare(self.doc,'source','project')
        archive=destination('previous');send=Mock(side_effect=TimeoutError('unknown outcome'))
        with self.assertRaises(ValueError):self.job.send_or_reconcile(record,self.doc,'archive','source','project',lambda _:archive,send,lambda:None,lambda _:self.doc)
        with self.assertRaises(ValueError):self.job.send_or_reconcile(record,self.doc,'archive','source','project',lambda _:archive,send,lambda:None,lambda _:self.doc)
        send.assert_called_once()

    def test_confirmed_no_send_can_be_retried_without_reupload(self):
        record=make_file_record(self.doc,'source','project');self.job.prepare(self.doc,'source','project')
        send=Mock(side_effect=NotSubmitted('not clicked'))
        for _ in range(2):
            with self.assertRaises(NotSubmitted):self.job.send_or_reconcile(record,self.doc,'archive','source','project',lambda _:destination('previous'),send,lambda:None,lambda _:self.doc)
        self.assertEqual(send.call_count,2);self.transfer.copy.assert_called_once()

    def test_source_changed_during_send_cannot_be_confirmed(self):
        record=make_file_record(self.doc,'source','project');self.job.prepare(self.doc,'source','project')
        cloud={'archive':destination('previous')};self.transfer._digest_file.return_value=hashlib.sha256(b'abc').hexdigest()
        def writer(project,cid,payload,files):
            archive=destination(payload);archive['mapping']['copy']['message']['metadata']['attachments']=[{k:files[0][k] for k in ('id','name','mime_type','size')}]
            cloud[cid]=archive;self.doc['mapping']['a']['message']['content']['parts'][0]+=' more'
            return cid
        with self.assertRaises(ValueError):
            self.job.send_or_reconcile(record,copy.deepcopy(self.doc),'archive','source','project',cloud.get,writer,lambda:None,lambda _:self.doc)
        import json
        state=json.loads((self.root/'file-job-source.json').read_text(encoding='utf-8'))
        self.assertEqual(state['submission']['state'],'sending')
