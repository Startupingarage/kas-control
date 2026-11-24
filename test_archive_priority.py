import json,time
from unittest.mock import Mock,patch
from test_archive_batch import BatchTests
from test_ordinary_archive import OrdinaryTests
from archive_worker import QueueMonitor,NotSubmitted
from archive_desktop import run,prepare_with_retry


class PriorityTests(__import__("unittest").TestCase):
    setUp=BatchTests.setUp
    def test_voice_stability_wait_cannot_be_spent_on_ordinary_scan(self):
        from test_voice_archive import destination
        clock=[100.0];events=[]
        def writer(project,cid,text):
            events.append('voice');self.cloud.docs['archive']=destination(text);return 'archive'
        def ordinary(*args,**kwargs):
            events.append('ordinary')
            self.assertEqual(self.cloud.deleted,['source'])
            return {'copied':0,'checked':0,'pending':0,'errors':[]}
        with patch('archive_desktop.Controller',return_value=self.controller),patch('archive_desktop.BrowserWriter',return_value=writer),patch('archive_profile.import_archive_login'),patch('ordinary_archive.batch',side_effect=ordinary),patch('archive_desktop.time.time',side_effect=lambda:clock[0]),patch('archive_desktop.time.monotonic',side_effect=lambda:clock[0]),patch('archive_desktop.time.sleep',side_effect=lambda n:clock.__setitem__(0,clock[0]+n)):
            run(Mock(),self.root,once=True)
        self.assertEqual(events,['voice','ordinary'])
        self.assertEqual(json.loads((self.root/'status.json').read_text())['voice_copied'],1)

    def test_long_cooldown_exits_instead_of_polling_for_six_minutes(self):
        (self.root/'ordinary-rate-limit.json').write_text(json.dumps({'retry_at':time.time()+900}))
        wait=Mock(side_effect=AssertionError('must exit'))
        self.assertFalse(prepare_with_retry(self.root,self.controller,limit=360,wait=wait))
        wait.assert_not_called()

    def test_preflight_network_retry_keeps_ordinary_scan_out_of_voice_wait(self):
        from archive_desktop import prepare_batch
        with patch.object(self.cloud,'read',side_effect=TimeoutError('network down')),patch('ordinary_archive.batch') as ordinary:
            self.assertFalse(prepare_batch(self.root,self.controller))
        ordinary.assert_not_called()

    def test_waiting_is_not_a_failed_chat(self):
        q=QueueMonitor(self.root/'observations.json')
        q.update('voice',10,error=ValueError('Voice is active or its state is not confirmed'))
        q.update('rate',10,error=ValueError('HTTP 429'))
        self.assertEqual(q.report([{'conversation_id':c} for c in ('voice','rate')])['blocked_sources'],0)

    def test_retry_after_is_not_replaced_by_fixed_five_minutes(self):
        (self.root/'ordinary-rate-limit.json').write_text(json.dumps({'retry_at':103}))
        q=QueueMonitor(self.root/'observations.json')
        with patch('chat_requests.time.time',return_value=100):
            q.update('source',100,error=ValueError('HTTP 429'))
        self.assertEqual(q.items['source']['retry_at'],103)

    def test_verified_copy_is_counted_even_when_delete_gets_429(self):
        from test_voice_archive import destination
        clock=[100.0]
        def writer(project,cid,text):
            self.cloud.docs['archive']=destination(text);return 'archive'
        with patch('archive_desktop.Controller',return_value=self.controller),patch('archive_desktop.BrowserWriter',return_value=writer),patch('archive_profile.import_archive_login'),patch('ordinary_archive.projects',return_value={}),patch.object(self.cloud,'delete',side_effect=RuntimeError('HTTP 429')),patch('archive_desktop.time.time',side_effect=lambda:clock[0]),patch('archive_desktop.time.monotonic',side_effect=lambda:clock[0]),patch('archive_desktop.time.sleep',side_effect=lambda n:clock.__setitem__(0,clock[0]+n)):
            run(Mock(),self.root,once=True,deadline=200)
        self.assertEqual(json.loads((self.root/'status.json').read_text())['voice_copied'],1)
        self.assertIn('source',self.cloud.docs)


class DraftBatchTests(__import__("unittest").TestCase):
    setUp=OrdinaryTests.setUp
    write=OrdinaryTests.write
    def test_project_draft_is_tried_only_once_per_run(self):
        self.cloud.chats.side_effect=lambda p:['a','b','c'] if p=='project' else ['other']
        def process(cid,project):
            if project=='project':raise NotSubmitted('Existing composer draft retained; no overwrite')
            return 'unchanged'
        blocked=set()
        from ordinary_archive import batch
        with patch('ordinary_archive.projects',return_value={'project':True,'later':True}),patch('ordinary_archive.OrdinaryArchive.process',side_effect=process) as work:
            first=batch(self.worker,[],{'phase':'idle'},self.root,blocked_projects=blocked)
            batch(self.worker,[],{'phase':'idle'},self.root,blocked_projects=blocked)
        self.assertEqual(sum(c.args[1]=='project' for c in work.call_args_list),1)
        self.assertEqual(len(first['errors']),1)
        self.cloud.delete.assert_not_called()


del BatchTests,OrdinaryTests
