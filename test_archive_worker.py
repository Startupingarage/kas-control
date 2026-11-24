import copy
import tempfile
import unittest
from pathlib import Path

from archive_worker import ArchiveWorker, NotSubmitted, verified_archive_draft
from test_voice_archive import source, destination


class Cloud:
    def __init__(self): self.docs={'source':source()};self.deleted=[]
    def read(self,cid): return copy.deepcopy(self.docs.get(cid))
    def chats(self,project): return [cid for cid,d in self.docs.items() if d['gizmo_id']==project]
    def rename(self,cid,title): self.docs[cid]['title']=title
    def delete(self,cid): self.deleted.append(cid);del self.docs[cid]


class ArchiveWorkerTests(unittest.TestCase):
    def test_resuming_verified_copy_reads_archive_then_one_fresh_source(self):
        from unittest.mock import patch
        self.process(0);self.busy_after_send=True
        with self.assertRaises(ValueError):self.process(31)
        self.status['phase']='idle';self.busy_after_send=False
        with patch.object(self.cloud,'read',wraps=self.cloud.read) as read:
            self.assertEqual(self.process(62),'deleted')
        self.assertEqual([call.args[0] for call in read.call_args_list],['archive','source','source'])

    def prepare_pair(self,project='project'):
        extra={**self.identity,'id':'other-slot','conversation_id':'source-two','gizmo_id':project}
        self.cloud.docs['source-two']={**source(),'conversation_id':'source-two','gizmo_id':project}
        if project!='project':self.routes['routes'].append({'project_id':project,'chat_id':'other-new'})
        self.worker.closed_state=lambda:([self.identity,extra],self.status,self.routes)
        self.process(0);self.worker.process(extra,0)
        return extra

    def test_stable_voice_jobs_in_one_project_share_a_write_and_verify_each_source(self):
        extra=self.prepare_pair()
        self.process(31)
        self.assertEqual(self.worker.job('source-two')['state'],'copied')
        self.assertEqual(self.worker.process(extra,32),'deleted')
        self.assertEqual(len(self.writes),1)
        self.assertEqual(self.cloud.deleted,['source','source-two'])

    def test_batch_never_includes_another_project(self):
        self.prepare_pair('other-project');self.process(31)
        self.assertEqual(self.worker.job('source-two')['state'],'pending')
        self.assertNotIn('source-two',self.writes[0])

    def test_uncertain_batch_is_reconciled_without_resending(self):
        extra=self.prepare_pair();self.fail_after_send=True
        with self.assertRaises(RuntimeError):self.process(31)
        self.assertEqual(self.worker.job('source-two')['state'],'submitting')
        self.fail_after_send=False
        self.process(32);self.worker.process(extra,33)
        self.assertEqual(len(self.writes),1)
        self.assertEqual(self.cloud.deleted,['source','source-two'])

    def test_batch_member_changed_after_copy_is_retained(self):
        extra=self.prepare_pair();self.process(31)
        self.cloud.docs['source-two']['mapping']['a']['message']['content']['parts'][0]+=' new text'
        self.assertEqual(self.worker.process(extra,32),'pending')
        self.assertNotIn('source-two',self.cloud.deleted)

    def test_partial_batch_copy_does_not_mark_all_members_copied(self):
        from voice_archive import archive_message
        self.prepare_pair()
        self.worker.write=lambda project,cid,text:self.write(project,cid,archive_message([self.worker.job('source')['record']]))
        with self.assertRaises(ValueError):self.process(31)
        self.assertFalse(self.cloud.deleted)
        self.assertEqual([self.worker.job(cid)['state'] for cid in ('source','source-two')],['submitting','submitting'])

    def test_batch_failure_before_send_restores_all_pending_jobs(self):
        self.prepare_pair()
        def reject(*args):raise NotSubmitted('No send')
        self.worker.write=reject
        with self.assertRaises(NotSubmitted):self.process(31)
        self.assertEqual([self.worker.job(cid)['state'] for cid in ('source','source-two')],['pending','pending'])
        self.assertFalse(self.cloud.deleted)

    def test_batch_draft_is_recoverable_only_when_every_record_matches_known_snapshots(self):
        from archive_worker import recoverable_archive_draft
        from voice_archive import archive_message
        self.prepare_pair()
        text=archive_message([self.worker.job(cid)['record'] for cid in ('source-two','source')])
        self.assertTrue(recoverable_archive_draft(text,self.worker.root,'project'))
        self.assertFalse(recoverable_archive_draft(text+' user note',self.worker.root,'project'))
        self.assertFalse(recoverable_archive_draft(text,self.worker.root,'other-project'))
        (self.worker.root/self.worker.job('source-two')['snapshot']).unlink()
        self.assertFalse(recoverable_archive_draft(text,self.worker.root,'project'))

    def test_batch_excludes_unstable_or_still_routable_member(self):
        for kind in ('unstable','routable'):
            with self.subTest(kind=kind):
                self.setUp();self.prepare_pair()
                if kind=='unstable':
                    job=self.worker.job('source-two');job['stable_since']=30;self.worker.save('source-two',job)
                else:self.routes['routes'].append({'project_id':'project','chat_id':'other-slot'})
                self.process(31)
                self.assertNotIn('source-two',self.writes[0])
                self.assertEqual(self.worker.job('source-two')['state'],'pending')

    def test_complete_cloud_readback_is_reused_but_source_is_rechecked_before_delete(self):
        from unittest.mock import patch
        self.process(0)
        def write(project,cid,text):
            result=self.write(project,cid,text)
            write.confirmed_archive=copy.deepcopy(self.cloud.docs[result])
            return result
        self.worker.write=write
        with patch.object(self.cloud,'read',wraps=self.cloud.read) as read:
            self.process(31)
        self.assertEqual(sum(c.args[0]=='archive' for c in read.call_args_list),0)
        self.assertGreaterEqual(sum(c.args[0]=='source' for c in read.call_args_list),2)
        self.assertEqual(self.cloud.deleted,['source'])
    def empty_source(self):
        self.cloud.docs['source']={'conversation_id':'source','gizmo_id':'project','title':'New chat',
            'create_time':100,'update_time':101,'current_node':'root',
            'mapping':{'root':{'id':'root','parent':None,'message':None,'children':[]}}}
        self.identity['parent_message_id']=''

    def test_empty_session_requires_age_stability_and_verified_backup(self):
        self.empty_source()
        with self.assertRaises(ValueError):self.process(102)
        self.assertFalse(self.writes);self.assertFalse(self.cloud.deleted)
        self.assertEqual(self.process(86502),'pending')
        self.assertEqual(self.process(86533),'deleted')
        self.assertIn('empty_session',self.writes[0])
        self.assertEqual(self.cloud.deleted,['source'])

    def test_empty_session_with_known_parent_or_changed_timestamp_is_retained(self):
        self.empty_source();self.identity['parent_message_id']='known-message'
        with self.assertRaises(ValueError):self.process(86502)
        self.identity['parent_message_id']='';self.process(86502)
        self.cloud.docs['source']['update_time']=86520
        with self.assertRaises(ValueError):self.process(86533)
        self.assertFalse(self.cloud.deleted);self.assertFalse(self.writes)

    def test_empty_session_gaining_message_after_copy_is_not_deleted(self):
        self.empty_source();self.process(86502)
        original=self.worker.write
        def write(*args):
            result=original(*args);self.cloud.docs['source']=source();return result
        self.worker.write=write
        with self.assertRaises(ValueError):self.process(86533)
        self.assertFalse(self.cloud.deleted)

    def test_empty_session_uncertain_send_is_reconciled_without_duplicate(self):
        self.empty_source();self.process(86502);self.fail_after_send=True
        with self.assertRaises(RuntimeError):self.process(86533)
        self.assertFalse(self.cloud.deleted)
        self.assertEqual(self.process(86540),'deleted')
        self.assertEqual(len(self.writes),1)

    def test_empty_session_with_unknown_nodes_or_missing_dates_is_retained(self):
        from test_voice_archive import message
        for kind in ('hidden','assistant','root_child','unknown_root','missing_date','future_date','invalid_date','unmanaged'):
            self.empty_source();self.identity['project_session']=True
            doc=self.cloud.docs['source']
            if kind in ('hidden','assistant'):
                doc['mapping']['root']['message']=message('assistant',['data'],channel='analysis' if kind=='hidden' else 'final')
            if kind=='root_child':doc['mapping']['root']['children']=['unknown']
            if kind=='unknown_root':doc['mapping']['root']['unknown']='data'
            if kind=='missing_date':doc.pop('update_time')
            if kind=='future_date':doc['update_time']=90000
            if kind=='invalid_date':doc['update_time']=float('nan')
            if kind=='unmanaged':self.identity['project_session']=False
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):self.process(86502)
        self.assertFalse(self.cloud.deleted);self.assertFalse(self.writes)

    def test_recover_damaged_generated_draft_but_preserve_edits_and_other_project(self):
        import json,html
        from archive_worker import recoverable_archive_draft
        from ordinary_archive import payload
        root=Path(self.temp.name)
        record={'source_id':'source','project_id':'project','revision':1,'messages':[{'value':'literal &#x20; &amp;'}]}
        (root/'ordinary-source.json').write_text(json.dumps({'source_id':'source','project_id':'project','pending':{'state':'submitting','record':record}}),encoding='utf-8')
        text=html.unescape(payload(record))
        self.assertTrue(recoverable_archive_draft(text,root,'project'))
        self.assertFalse(recoverable_archive_draft(text+' user edit',root,'project'))
        self.assertFalse(recoverable_archive_draft(text,root,'other'))
        self.assertFalse(recoverable_archive_draft('my own draft',root,'project'))

    def test_only_exact_backed_archive_drafts_may_be_replaced(self):
        self.process(0);self.busy_after_send=True
        with self.assertRaises(ValueError):self.process(31)
        root=Path(self.temp.name);text=self.writes[0]
        self.assertTrue(verified_archive_draft(text,root))
        self.assertFalse(verified_archive_draft('User draft',root))
        self.assertFalse(verified_archive_draft(text+' changed',root))
        snapshot=root/self.worker.job('source')['snapshot'];snapshot.write_text('{}')
        self.assertFalse(verified_archive_draft(text,root))
        self.assertFalse(self.cloud.deleted)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.cloud=Cloud();self.writes=[];self.fail_after_send=False;self.busy_after_send=False
        self.identity={'id':'old-slot','project_session':True,'conversation_id':'source','gizmo_id':'project'}
        self.status={'agent_connected':True,'phase':'idle','native':{'mode':'idle'},'error':''}
        self.routes={'project_sessions':True,'routes':[{'project_id':'project','chat_id':'next-slot'}]}
        self.worker=ArchiveWorker(Path(self.temp.name),self.cloud,self.write,self.closed_state)
    def closed_state(self):return [self.identity],self.status,self.routes
    def write(self,project,cid,text):
        self.writes.append(text)
        self.cloud.docs['archive']=destination(text)
        if self.busy_after_send:self.status['phase']='active'
        if self.fail_after_send:raise RuntimeError('Connection lost after submitting')
        return 'archive'
    def process(self,now):return self.worker.process(self.identity,now)
    def test_full_cycle_waits_for_stability_and_verified_copy(self):
        self.process(0);self.assertFalse(self.writes)
        self.process(29);self.assertFalse(self.writes)
        self.process(31)
        self.assertEqual(self.cloud.deleted,['source'])
        self.assertEqual(self.worker.job('source')['state'],'deleted')
        self.process(60);self.assertEqual(len(self.writes),1)
    def test_image_is_marked_and_source_is_deleted_only_after_text_copy_verifies(self):
        msg=self.cloud.docs['source']['mapping']['u']['message']
        msg['metadata']['attachments']=[{'id':'file_123','mime_type':'image/jpeg','size':10}]
        msg['content']['parts'].append({'content_type':'image_asset_pointer','asset_pointer':'sediment://file_123','mime_type':'image/jpeg','size_bytes':10})
        self.process(0);self.assertFalse(self.cloud.deleted)
        self.process(31)
        self.assertIn('Изображение пропущено',self.writes[0])
        self.assertEqual(self.cloud.deleted,['source'])
    def test_uncertain_submit_reconciles_without_second_write(self):
        self.process(0);self.fail_after_send=True
        with self.assertRaises(RuntimeError):self.process(31)
        self.assertFalse(self.cloud.deleted)
        self.process(40)
        self.assertEqual(len(self.writes),1)
        self.assertEqual(self.cloud.deleted,['source'])
    def test_active_after_copy_defers_deletion(self):
        self.process(0);self.busy_after_send=True
        with self.assertRaises(ValueError):self.process(31)
        self.assertFalse(self.cloud.deleted)
        self.status['phase']='idle';self.process(50)
        self.assertEqual(len(self.writes),1)
        self.assertEqual(self.cloud.deleted,['source'])
    def test_changed_source_restarts_stability_before_copy(self):
        self.process(0)
        self.cloud.docs['source']['mapping']['a']['message']['content']['parts'][0]+=' added'
        self.process(31);self.assertFalse(self.writes)
        self.process(62);self.assertEqual(len(self.writes),1)
        self.assertIn(' added',self.writes[0])
    def test_archive_corruption_prevents_deletion(self):
        self.process(0);self.busy_after_send=True
        with self.assertRaises(ValueError):self.process(31)
        self.status['phase']='idle';self.cloud.docs['archive']['mapping']['copy']['message']['content']['parts']=['truncated']
        with self.assertRaises(ValueError):self.process(50)
        self.assertFalse(self.cloud.deleted)
    def test_empty_archive_is_never_considered_proof(self):
        self.process(0)
        def lose(project,cid,text):raise RuntimeError('No write acknowledgement')
        self.worker.write=lose
        with self.assertRaises(RuntimeError):self.process(31)
        with self.assertRaises(ValueError):self.process(50)
        self.assertFalse(self.cloud.deleted)
    def test_restart_after_delete_reconciles_receipt(self):
        self.process(0);self.process(31)
        state=self.worker.job('source');state['state']='deleting';self.worker.save('source',state)
        worker=ArchiveWorker(Path(self.temp.name),self.cloud,self.write,self.closed_state)
        worker.process(self.identity,80)
        self.assertEqual(worker.job('source')['state'],'deleted')
        self.assertEqual(len(self.cloud.deleted),1)

    def test_deleted_source_recovers_from_durable_proof_without_refetching_reserve(self):
        from unittest.mock import patch
        self.process(0);self.process(31)
        job=self.worker.job('source');job['state']='deleting';self.worker.save('source',job)
        real_read=self.cloud.read
        def read(cid):
            if cid==job['archive_id']:raise AssertionError('Reserve was already verified before deletion')
            return real_read(cid)
        with patch.object(self.cloud,'read',side_effect=read):
            self.assertEqual(self.process(60),'deleted')
        self.assertEqual(len(self.cloud.deleted),1)

    def test_recovery_rejects_incomplete_durable_proof(self):
        import json
        self.process(0);self.process(31)
        job=self.worker.job('source');job['state']='deleting';self.worker.save('source',job)
        path=self.worker.root/('copy-proof-source-'+job['record']['sha256']+'.json')
        proof=json.loads(path.read_text(encoding='utf-8'));proof['mapping']={}
        path.write_text(json.dumps(proof),encoding='utf-8')
        with self.assertRaises(ValueError):self.process(60)
        self.assertEqual(self.worker.job('source')['state'],'deleting')
        self.assertEqual(len(self.cloud.deleted),1)
    def test_failure_before_submission_can_retry(self):
        self.process(0)
        def offline(*args):raise NotSubmitted('No composer, no message sent')
        self.worker.write=offline
        with self.assertRaises(NotSubmitted):self.process(31)
        self.assertEqual(self.worker.job('source')['state'],'pending')
        self.worker.write=self.write;self.process(65)
        self.assertEqual(len(self.writes),1)
        self.assertEqual(self.cloud.deleted,['source'])
    def test_moved_archive_blocks_before_private_text_is_sent(self):
        self.cloud.docs['archive']=destination('Earlier entry')
        self.worker._bind('project','archive')
        self.process(0)
        self.cloud.docs['archive']['gizmo_id']='other-project'
        with self.assertRaises(ValueError):self.process(31)
        self.assertFalse(self.writes)
        self.assertFalse(self.cloud.deleted)
    def test_source_growing_during_acknowledged_write_can_finish(self):
        self.process(0)
        def write_and_grow(*args):
            cid=self.write(*args)
            self.cloud.docs['source']['mapping']['a']['message']['content']['parts'][0]+=' late text'
            return cid
        self.worker.write=write_and_grow
        with self.assertRaises(ValueError):self.process(31)
        self.worker.write=self.write
        self.process(62);self.process(93)
        self.assertEqual(self.cloud.deleted,['source'])
        self.assertEqual(len(self.writes),2)
        self.assertIn(' late text',self.writes[-1])
    def test_source_growing_during_uncertain_write_reconciles_old_copy(self):
        self.process(0)
        def write_grow_disconnect(*args):
            self.write(*args)
            self.cloud.docs['source']['mapping']['a']['message']['content']['parts'][0]+=' late text'
            raise RuntimeError('Disconnected after persistence')
        self.worker.write=write_grow_disconnect
        with self.assertRaises(RuntimeError):self.process(31)
        self.worker.write=self.write
        self.process(62);self.process(93)
        self.assertEqual(self.cloud.deleted,['source'])
        self.assertEqual(len(self.writes),2)


if __name__=='__main__':unittest.main()
