import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

from archive_attachment_worker import AttachmentArchiveWorker
from test_archive_worker import Cloud
from test_voice_archive import destination


class AttachmentWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.cloud=Cloud()
        msg=self.cloud.docs['source']['mapping']['u']['message']
        msg['metadata']['attachments']=[{'id':'file_a','name':'a.pdf','mime_type':'application/pdf','size':3}]
        self.cloud.docs['archive']=destination('previous')
        self.identity={'id':'old-slot','project_session':True,'conversation_id':'source','gizmo_id':'project'}
        self.status={'agent_connected':True,'phase':'idle','native':{'mode':'idle'},'error':''}
        self.routes={'project_sessions':True,'routes':[{'project_id':'project','chat_id':'next-slot'}]}
        self.transfer=Mock();self.transfer.MAX_FILE_BYTES=512*1024*1024
        self.transfer.copy.return_value={'source_id':'file_a','id':'file_new','name':'a.pdf','mime_type':'application/pdf','size':3,'sha256':hashlib.sha256(b'abc').hexdigest()}
        self.transfer._digest_file.return_value=hashlib.sha256(b'abc').hexdigest();self.writes=[];self.after_send=None
        self.worker=AttachmentArchiveWorker(self.root,self.cloud,self.write,self.closed_state,self.transfer)
        self.worker._bind('project','archive')

    def closed_state(self):return [self.identity],self.status,self.routes
    def write(self,project,cid,payload,files):
        self.writes.append(payload)
        self.cloud.docs[cid]=destination(payload)
        self.cloud.docs[cid]['mapping']['copy']['message']['metadata']['attachments']=[{k:f[k] for k in ('id','name','mime_type','size')} for f in files]
        if self.after_send:self.after_send()
        return cid
    def process(self,now):return self.worker.process(self.identity,now)

    def test_copy_proof_precedes_deletion(self):
        self.assertEqual(self.process(0),'pending');self.assertFalse(self.writes)
        real_delete=self.cloud.delete
        def delete(cid):
            job=self.worker.job(cid)
            self.assertEqual(job['state'],'deleting')
            self.assertTrue((self.root/job['file_proof']).exists())
            real_delete(cid)
        self.cloud.delete=Mock(side_effect=delete)
        self.assertEqual(self.process(31),'deleted')
        self.assertEqual(len(self.writes),1)
        self.assertEqual(self.worker.job('source')['state'],'deleted')

    def test_bad_bytes_never_delete_source(self):
        self.process(0);self.transfer._digest_file.return_value='b'*64
        with self.assertRaises(ValueError):self.process(31)
        self.assertFalse(self.cloud.deleted)

    def test_voice_becoming_active_after_send_defers_deletion(self):
        self.process(0);self.after_send=lambda:self.status.update(phase='active')
        with self.assertRaises(ValueError):self.process(31)
        self.assertFalse(self.cloud.deleted)
        self.status['phase']='idle';self.after_send=None
        self.assertEqual(self.process(45),'deleted')
        self.assertEqual(len(self.writes),1)

    def test_uncertain_send_does_not_repeat(self):
        self.process(0)
        self.after_send=lambda:(_ for _ in ()).throw(TimeoutError('response lost'))
        self.assertEqual(self.process(31),'deleted')
        self.assertEqual(len(self.writes),1)

    def test_missing_file_or_changed_text_prevents_deletion(self):
        self.process(0)
        def change():self.cloud.docs['source']['mapping']['a']['message']['content']['parts'][0]+=' changed'
        self.after_send=change
        with self.assertRaises(ValueError):self.process(31)
        self.assertFalse(self.cloud.deleted)

    def test_lost_delete_response_reconciles_without_deleting_twice(self):
        self.process(0);real_delete=self.cloud.delete
        def fail_after_delete(cid):real_delete(cid);raise TimeoutError('delete response lost')
        self.cloud.delete=Mock(side_effect=fail_after_delete)
        with self.assertRaises(TimeoutError):self.process(31)
        self.assertEqual(self.worker.job('source')['state'],'deleting')
        self.assertEqual(self.process(50),'deleted')
        self.cloud.delete.assert_called_once()

    def test_unavailable_receipt_prevents_deletion(self):
        self.process(0)
        from archive_attachment_worker import atomic_json
        def fail_proof(path,data):
            if Path(path).name.startswith('file-proof-'):raise OSError('disk unavailable')
            return atomic_json(path,data)
        with patch('archive_attachment_worker.atomic_json',side_effect=fail_proof):
            with self.assertRaises(OSError):self.process(31)
        self.assertFalse(self.cloud.deleted)

    def test_unconfirmed_delete_with_source_present_is_never_repeated(self):
        self.process(0);self.cloud.delete=Mock(side_effect=TimeoutError('unknown outcome'))
        with self.assertRaises(TimeoutError):self.process(31)
        with self.assertRaises(ValueError):self.process(50)
        self.cloud.delete.assert_called_once()

    def test_lost_delete_reply_needs_fresh_archive_and_file_verification(self):
        self.process(0);real_delete=self.cloud.delete
        def fail_after_delete(cid):real_delete(cid);raise TimeoutError('delete response lost')
        self.cloud.delete=Mock(side_effect=fail_after_delete)
        with self.assertRaises(TimeoutError):self.process(31)
        self.transfer._digest_file.return_value='b'*64
        with self.assertRaises(ValueError):self.process(50)
        self.assertEqual(self.worker.job('source')['state'],'deleting')
        self.cloud.delete.assert_called_once()
