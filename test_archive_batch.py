import tempfile,time,unittest,json
from pathlib import Path
from unittest.mock import Mock,patch
from archive_desktop import prepare_batch,prepare_with_retry
from archive_worker import ArchiveWorker,QueueMonitor
from test_archive_worker import Cloud

class BatchTests(unittest.TestCase):
    def test_ordinary_new_chat_requests_browser_but_managed_chat_is_not_duplicated(self):
        self.identities=[];self.cloud.chats=lambda project:['source']
        with patch('ordinary_archive.projects',return_value={'project':True}):
            self.assertTrue(prepare_batch(self.root,self.controller))
        self.assertEqual(json.loads((self.root/'ordinary-source.json').read_text(encoding='utf-8'))['pending']['state'],'prepared')
        self.assertFalse(self.cloud.deleted)

    def test_ordinary_no_changes_exits_without_requesting_browser(self):
        from ordinary_archive import OrdinaryArchive
        from test_voice_archive import destination
        self.identities=[]
        def writer(project,cid,text):self.cloud.docs['archive']=destination(text);return 'archive'
        worker=ArchiveWorker(self.root,self.cloud,writer,lambda:None);ordinary=OrdinaryArchive(worker)
        ordinary.process('source','project');ordinary.process('source','project')
        self.cloud.chats=lambda project:['source','archive']
        with patch('ordinary_archive.projects',return_value={'project':True}):
            self.assertFalse(prepare_batch(self.root,self.controller))
        self.assertFalse(self.cloud.deleted)

    def test_ordinary_only_waits_for_voice_to_close_before_requesting_browser(self):
        self.identities=[];self.status['phase']='active';waits=[]
        def wait(seconds):waits.append(seconds);self.status['phase']='idle'
        with patch('ordinary_archive.projects',return_value={'project':True}):
            self.assertTrue(prepare_with_retry(self.root,self.controller,wait=wait))
        self.assertEqual(waits,[15]);self.assertFalse(self.cloud.deleted)

    def test_ordinary_only_retries_temporary_read_and_stops_at_limit(self):
        self.identities=[];real_read=self.cloud.read;attempts=[];waits=[]
        def read(cid):
            attempts.append(cid)
            if len(attempts)==1:raise TimeoutError('network down')
            return real_read(cid)
        with patch('ordinary_archive.projects',return_value={'project':True}),patch.object(self.cloud,'read',side_effect=read):
            self.assertTrue(prepare_with_retry(self.root,self.controller,wait=lambda seconds:waits.append(seconds)))
        self.assertEqual(waits,[15]);self.assertFalse(self.cloud.deleted)
        with patch('ordinary_archive.projects',return_value={'project':True}),patch.object(self.cloud,'read',side_effect=TimeoutError('still down')):
            self.assertFalse(prepare_with_retry(self.root,self.controller,limit=0,wait=Mock(side_effect=AssertionError('bounded'))))
        self.assertEqual(json.loads((self.root/'status.json').read_text())['note'],'incomplete')

    def test_ordinary_rate_limit_blocks_voice_reads_in_preflight(self):
        (self.root/'ordinary-rate-limit.json').write_text(json.dumps({'retry_at':time.time()+300}),encoding='utf-8')
        with patch('ordinary_archive.projects',return_value={'project':True}), \
             patch.object(self.cloud,'read',wraps=self.cloud.read) as read, \
             patch.object(self.cloud,'chats',wraps=self.cloud.chats) as chats:
            self.assertFalse(prepare_batch(self.root,self.controller));read.assert_not_called();chats.assert_not_called()
        self.assertEqual(json.loads((self.root/'status.json').read_text())['note'],'rate_limited')

    def test_preflight_discovery_respects_global_deadline_before_reading_sources(self):
        self.identities=[];clock=[0]
        def chats(project):clock[0]=2;return ['source']
        with patch('ordinary_archive.projects',return_value={'project':True}), \
             patch.object(self.cloud,'chats',side_effect=chats),patch.object(self.cloud,'read',wraps=self.cloud.read) as read, \
             patch('archive_desktop.time.monotonic',side_effect=lambda:clock[0]):
            self.assertFalse(prepare_with_retry(self.root,self.controller,limit=1,wait=Mock(side_effect=AssertionError('deadline'))))
            read.assert_not_called()
        final=json.loads((self.root/'status.json').read_text())
        self.assertEqual(final['note'],'incomplete');self.assertEqual(final['ordinary']['deferred'],'batch_limit')

    def test_browser_phase_uses_remaining_shared_budget_instead_of_new_six_minutes(self):
        from archive_desktop import run
        clock=[459.0];window=Mock()
        with patch('archive_desktop.Controller',return_value=self.controller), \
             patch('archive_desktop.BrowserWriter',return_value=Mock(side_effect=AssertionError('no time to submit'))), \
             patch('archive_profile.import_archive_login'),patch('ordinary_archive.projects',return_value={}), \
             patch.object(self.cloud,'read',wraps=self.cloud.read) as read, \
             patch('archive_desktop.time.time',side_effect=lambda:clock[0]), \
             patch('archive_desktop.time.monotonic',side_effect=lambda:clock[0]), \
             patch('archive_desktop.time.sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)):
            run(window,self.root,once=True,deadline=460)
        self.assertEqual(clock[0],460);read.assert_called_once_with('source')
        self.assertEqual(json.loads((self.root/'status.json').read_text())['note'],'incomplete')
        self.assertFalse(self.cloud.deleted);window.destroy.assert_called_once()

    def test_main_passes_identical_deadline_to_preflight_and_browser(self):
        import sys
        from types import SimpleNamespace
        from archive_desktop import main
        clock=[100.0];window=Mock();webview=Mock();webview.create_window.return_value=window
        webview.start.side_effect=lambda callback,**kwargs:callback()
        forms=SimpleNamespace(BrowserView=SimpleNamespace(BrowserForm=object))
        def prepare(*args,**kwargs):clock[0]=300;return True
        with patch.object(sys,'argv',['archive_desktop.py','--once','--state',str(self.root)]), \
             patch.dict(sys.modules,{'webview':webview,'webview.platforms':SimpleNamespace(winforms=forms)}), \
             patch('archive_desktop.Controller',return_value=self.controller), \
             patch('archive_desktop.prepare_with_retry',side_effect=prepare) as preflight, \
             patch('archive_desktop.run') as browser, \
             patch('archive_desktop.time.monotonic',side_effect=lambda:clock[0]):
            main()
        self.assertEqual(preflight.call_args.kwargs['deadline'],460)
        browser.assert_called_once_with(window,self.root,True,deadline=460)

    def test_expired_browser_handoff_preserves_preflight_counts_without_cloud_reads(self):
        from archive_desktop import run
        report={'note':'browser_needed','voice_phase':'idle','ordinary':{'copied':2,'checked':5,'pending':1,'errors':[]}}
        (self.root/'status.json').write_text(json.dumps(report))
        with patch('archive_desktop.Controller',return_value=self.controller), \
             patch('archive_desktop.time.monotonic',return_value=460), \
             patch.object(self.cloud,'read',wraps=self.cloud.read) as read:
            run(Mock(),self.root,once=True,deadline=460)
        read.assert_not_called();final=json.loads((self.root/'status.json').read_text())
        self.assertEqual(final['note'],'incomplete');self.assertEqual(final['ordinary']['copied'],2)
        self.assertEqual(final['ordinary']['checked'],5);self.assertEqual(final['ordinary']['pending'],1)

    def test_preflight_final_rate_limit_preserves_previous_success_counts_and_error(self):
        clock=[100.0];attempts=[]
        def prepare(root,controller,deadline=None):
            attempts.append(clock[0]);limited=len(attempts)>1
            ordinary={'copied':0 if limited else 1,'checked':0 if limited else 2,'pending':0,
                      'retry_after':300 if limited else 30,'errors':[{'detail':'HTTP 429'}] if limited else []}
            if limited:
                clock[0]=106;(root/'ordinary-rate-limit.json').write_text(json.dumps({'retry_at':406}))
            (root/'status.json').write_text(json.dumps({'remaining_sources':0,'note':'rate_limited' if limited else 'attention','ordinary':ordinary}))
            return False
        with patch('archive_desktop.prepare_batch',side_effect=prepare), \
             patch('archive_desktop.time.monotonic',side_effect=lambda:clock[0]), \
             patch('archive_desktop.time.time',side_effect=lambda:clock[0]):
            self.assertFalse(prepare_with_retry(self.root,self.controller,deadline=106,wait=lambda seconds:clock.__setitem__(0,clock[0]+seconds)))
        final=json.loads((self.root/'status.json').read_text())
        self.assertEqual(final['note'],'rate_limited');self.assertEqual(final['ordinary']['copied'],1)
        self.assertEqual(final['ordinary']['checked'],2);self.assertEqual(final['ordinary']['errors'],[{'detail':'HTTP 429'}])

    def test_ordinary_running_status_precedes_send_and_final_counts_cover_all_passes(self):
        from archive_desktop import run
        from test_voice_archive import destination
        self.identities=[];clock=[100.0];window=Mock();seen=[]
        def writer(project,cid,text):
            report=json.loads((self.root/'status.json').read_text())
            seen.append(report)
            self.assertEqual(report['note'],'running');self.assertNotIn('completed_at',report)
            self.assertEqual(report['ordinary']['current']['source_id'],'source')
            self.assertEqual(report['ordinary']['current']['state'],'send')
            self.cloud.docs['archive']=destination(text);return 'archive'
        with patch('archive_desktop.Controller',return_value=self.controller), \
             patch('archive_desktop.BrowserWriter',return_value=writer), \
             patch('archive_profile.import_archive_login'), \
             patch('ordinary_archive.projects',return_value={'project':True}), \
             patch('archive_desktop.time.time',side_effect=lambda:clock[0]), \
             patch('archive_desktop.time.monotonic',side_effect=lambda:clock[0]), \
             patch('archive_desktop.time.sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)):
            run(window,self.root,once=True)
        final=json.loads((self.root/'status.json').read_text())
        self.assertEqual(len(seen),1);self.assertEqual(final['note'],'checked')
        self.assertEqual(final['ordinary']['copied'],1);self.assertEqual(final['ordinary']['checked'],2)
        self.assertFalse(self.cloud.deleted);self.assertIn('source',self.cloud.docs)

    def test_ordinary_success_counts_survive_following_rate_limited_pass(self):
        from archive_desktop import run
        clock=[100.0];self.identities=[];calls=[]
        def ordinary(*args,**kwargs):
            calls.append(clock[0])
            value={'copied':1 if len(calls)==1 else 0,'checked':1 if len(calls)==1 else 0,
                   'pending':1 if len(calls)==1 else 0,'errors':[]}
            if len(calls)>1:
                (self.root/'ordinary-rate-limit.json').write_text(json.dumps({'retry_at':clock[0]+300}))
                value.update(deferred='rate_limited',retry_after=300,errors=[{'detail':'HTTP 429'}])
                (self.root/'stop').touch()
            kwargs['progress'](value);return value
        with patch('archive_desktop.Controller',return_value=self.controller), \
             patch('archive_profile.import_archive_login'),patch('ordinary_archive.batch',side_effect=ordinary), \
             patch('archive_desktop.time.time',side_effect=lambda:clock[0]), \
             patch('archive_desktop.time.monotonic',side_effect=lambda:clock[0]), \
             patch('archive_desktop.time.sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)):
            run(Mock(),self.root,once=True)
        final=json.loads((self.root/'status.json').read_text())
        self.assertEqual(final['note'],'rate_limited')
        self.assertEqual(final['ordinary']['copied'],1);self.assertEqual(final['ordinary']['checked'],1)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.cloud=Cloud();self.controller=Mock()
        self.identity={'id':'old','conversation_id':'source','gizmo_id':'project','project_session':True}
        self.status={'agent_connected':True,'phase':'idle','native':{'mode':'idle'},'error':''}
        self.routes={'project_sessions':True,'routes':[{'project_id':'project','chat_id':'new'}]}
        self.identities=[self.identity]
        a=patch('archive_desktop.ArchiveCloud',return_value=self.cloud);a.start();self.addCleanup(a.stop)
        b=patch('archive_desktop.read_closed_state',side_effect=lambda *args:(self.identities,self.status,self.routes));b.start();self.addCleanup(b.stop)

    def test_no_sources_does_not_need_a_browser(self):
        self.identities=[]
        self.assertFalse(prepare_batch(self.root,self.controller))

    def test_new_real_dialog_is_snapshotted_before_browser_is_requested(self):
        self.assertTrue(prepare_batch(self.root,self.controller))
        job=json.loads((self.root/'job-source.json').read_text(encoding='utf-8'))
        self.assertEqual(job['state'],'pending');self.assertTrue((self.root/job['snapshot']).exists())
        self.assertFalse(self.cloud.deleted)

    def test_preflight_stages_other_voice_sources_before_opening_browser(self):
        from test_voice_archive import source
        self.cloud.docs['second']={**source(),'conversation_id':'second'}
        self.identities.append({**self.identity,'conversation_id':'second','id':'second-slot'})
        self.assertTrue(prepare_batch(self.root,self.controller))
        self.assertTrue((self.root/'job-source.json').exists())
        self.assertTrue((self.root/'job-second.json').exists())
        with patch.object(self.cloud,'read',wraps=self.cloud.read) as read:
            self.assertTrue(prepare_batch(self.root,self.controller));read.assert_not_called()

    def test_cached_empty_sources_do_not_start_browser_or_network_reads(self):
        q=QueueMonitor(self.root/'observations.json');q.update('source',time.time(),error=ValueError('No user transcript to archive'));q.report([self.identity])
        with patch.object(self.cloud,'read',wraps=self.cloud.read) as read:
            self.assertFalse(prepare_batch(self.root,self.controller));read.assert_not_called()

    def test_active_voice_defers_archive_without_a_browser(self):
        self.status['phase']='active'
        self.assertFalse(prepare_batch(self.root,self.controller));self.assertFalse(self.cloud.deleted)

    def test_waits_for_voice_to_close_then_processes_without_background_service(self):
        self.status['phase']='active'
        def wait(seconds):self.status['phase']='idle'
        self.assertTrue(prepare_with_retry(self.root,self.controller,wait=wait))
        self.assertEqual(json.loads((self.root/'job-source.json').read_text(encoding='utf-8'))['state'],'pending')
        self.assertFalse(self.cloud.deleted)

    def test_retry_timeout_records_attention_and_stops(self):
        self.status['phase']='active'
        self.assertFalse(prepare_with_retry(self.root,self.controller,limit=0,wait=lambda _:None))
        status=json.loads((self.root/'status.json').read_text(encoding='utf-8'))
        self.assertEqual(status['note'],'voice_active')
        self.assertFalse(self.cloud.deleted)

    def test_retry_wait_status_names_network_error_accurately(self):
        seen=[]
        def prepare(root,controller,deadline=None):
            q=QueueMonitor(root/'observations.json')
            q.update('source',time.time(),error=TimeoutError('timeout'))
            report=q.report([self.identity]);report['note']='attention'
            (root/'status.json').write_text(json.dumps(report),encoding='utf-8')
            return False
        def wait(_):
            seen.append(json.loads((self.root/'status.json').read_text(encoding='utf-8'))['note'])
            (self.root/'stop').touch()
        with patch('archive_desktop.prepare_batch',side_effect=prepare):
            self.assertFalse(prepare_with_retry(self.root,self.controller,wait=wait))
        self.assertEqual(seen,['waiting_connection'])

    def test_batch_resumes_after_voice_interrupts_copy_without_resending(self):
        from archive_desktop import run
        clock=[100.0];window=Mock();writes=[]
        def writer(project,cid,text):
            from test_voice_archive import destination
            self.cloud.docs['archive']=destination(text);writes.append(text)
            self.status['phase']='active'
            return 'archive'
        def sleep(seconds):
            clock[0]+=seconds
            if clock[0]>=155:self.status['phase']='idle'
        with patch('archive_desktop.Controller',return_value=self.controller), \
             patch('archive_desktop.BrowserWriter',return_value=writer), \
             patch('archive_profile.import_archive_login'), \
             patch('archive_desktop.time.time',side_effect=lambda:clock[0]), \
             patch('archive_desktop.time.monotonic',side_effect=lambda:clock[0]), \
             patch('archive_desktop.time.sleep',side_effect=sleep):
            run(window,self.root,once=True)
        self.assertEqual(len(writes),1)
        self.assertEqual(self.cloud.deleted,['source'])
        self.assertEqual(json.loads((self.root/'job-source.json').read_text(encoding='utf-8'))['state'],'deleted')
        window.destroy.assert_called_once()

    def test_batch_retries_transient_read_and_deletes_only_after_verification(self):
        from archive_desktop import run
        from test_voice_archive import destination
        clock=[100.0];window=Mock();writes=[];real_read=self.cloud.read;attempt=[0]
        def read(cid):
            if cid=='source' and attempt[0]==0:
                attempt[0]+=1;raise TimeoutError('network down')
            return real_read(cid)
        def writer(project,cid,text):
            writes.append(text);self.cloud.docs['archive']=destination(text);return 'archive'
        with patch('archive_desktop.Controller',return_value=self.controller), \
             patch('archive_desktop.BrowserWriter',return_value=writer), \
             patch('archive_profile.import_archive_login'), \
             patch.object(self.cloud,'read',side_effect=read), \
             patch('archive_desktop.time.time',side_effect=lambda:clock[0]), \
             patch('archive_desktop.time.monotonic',side_effect=lambda:clock[0]), \
             patch('archive_desktop.time.sleep',side_effect=lambda n:clock.__setitem__(0,clock[0]+n)):
            run(window,self.root,once=True)
        self.assertEqual(len(writes),1)
        self.assertEqual(self.cloud.deleted,['source'])
        self.assertEqual(json.loads((self.root/'job-source.json').read_text(encoding='utf-8'))['state'],'deleted')
        window.destroy.assert_called_once()

    def test_submission_timeout_reconciles_existing_copy_without_sending_twice(self):
        from archive_desktop import run
        from test_voice_archive import destination
        clock=[100.0];window=Mock();writes=[]
        def writer(project,cid,text):
            writes.append(text);self.cloud.docs['archive']=destination(text)
            raise TimeoutError('reply lost after submission')
        with patch('archive_desktop.Controller',return_value=self.controller), \
             patch('archive_desktop.BrowserWriter',return_value=writer), \
             patch('archive_profile.import_archive_login'), \
             patch('archive_desktop.time.time',side_effect=lambda:clock[0]), \
             patch('archive_desktop.time.monotonic',side_effect=lambda:clock[0]), \
             patch('archive_desktop.time.sleep',side_effect=lambda n:clock.__setitem__(0,clock[0]+n)):
            run(window,self.root,once=True)
        self.assertEqual(len(writes),1)
        self.assertEqual(self.cloud.deleted,['source'])
        self.assertEqual(json.loads((self.root/'job-source.json').read_text(encoding='utf-8'))['state'],'deleted')

    def test_old_pending_job_does_not_become_an_uncertain_submission(self):
        self.assertTrue(prepare_batch(self.root,self.controller))
        path=self.root/'job-source.json';job=json.loads(path.read_text(encoding='utf-8'));job['stable_since']=0;path.write_text(json.dumps(job),encoding='utf-8')
        self.assertTrue(prepare_batch(self.root,self.controller))
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['state'],'pending');self.assertFalse(self.cloud.deleted)

if __name__=='__main__':unittest.main()
