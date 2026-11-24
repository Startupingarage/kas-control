import unittest
from unittest.mock import Mock,patch
from chat_requests import ChatReadPacer
from archive_desktop import ArchiveCloud


class ChatRequestTests(unittest.TestCase):
    def test_history_reads_share_a_conservative_interval_across_clients(self):
        import tempfile
        from chat_requests import SharedChatReadPacer
        clock=[100];waits=[]
        def sleep(n):waits.append(n);clock[0]+=n
        with tempfile.TemporaryDirectory() as root:
            a=SharedChatReadPacer(root,clock=lambda:clock[0],sleep=sleep)
            a.wait()
            b=SharedChatReadPacer(root,clock=lambda:clock[0],sleep=sleep)
            b.wait();b.wait()
        self.assertEqual(waits,[65,65])

    def test_archive_does_not_load_project_catalog_to_create_http_session(self):
        controller=Mock();controller.station.status.return_value={'phase':'idle'}
        controller.cloud.http=None;transport=Mock();transport.get.return_value.status_code=200
        transport.get.return_value.json.return_value={'conversation_id':'source'}
        def connect():controller.cloud.http=transport;return transport
        controller.cloud.transport.side_effect=connect
        with patch('archive_desktop.require_cloud_ready'),patch('archive_desktop.pace_chat_read'):
            ArchiveCloud(controller).read('source')
        controller.cloud.projects.assert_not_called()

    def test_archive_skips_redundant_title_read_but_fresh_verification_still_reads(self):
        c=Mock();c.station.status.return_value={'phase':'idle'}
        response=c.cloud.http.get.return_value;response.status_code=200
        response.json.return_value={'conversation_id':'archive','title':'Reserve'}
        with patch('archive_desktop.require_cloud_ready'),patch('archive_desktop.pace_chat_read'):
            cloud=ArchiveCloud(c);cloud.read('archive');cloud.rename('archive','Reserve');cloud.read('archive')
        self.assertEqual(c.cloud.http.get.call_count,2)
        c.cloud.http.post.assert_not_called()

    def test_shared_pacer_serializes_independent_processes(self):
        import tempfile,subprocess,sys,json,os
        with tempfile.TemporaryDirectory() as root:
            code=('from chat_requests import SharedChatReadPacer; import sys,time,json; '
                  'p=SharedChatReadPacer(sys.argv[1],interval=.2); '
                  'p.wait(); print(json.dumps(time.time()))')
            children=[subprocess.Popen([sys.executable,'-c',code,root],stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE,text=True,creationflags=subprocess.CREATE_NO_WINDOW) for _ in range(4)]
            stamps=[]
            for child in children:
                out,err=child.communicate(timeout=15)
                self.assertEqual(child.returncode,0,err);stamps.append(json.loads(out))
            stamps.sort()
            self.assertTrue(all(b-a>=.18 for a,b in zip(stamps,stamps[1:])),stamps)

    def test_shared_pacer_survives_recreation_and_deferral(self):
        import tempfile
        from chat_requests import SharedChatReadPacer
        clock=[100];waits=[]
        def sleep(delay):waits.append(delay);clock[0]+=delay
        with tempfile.TemporaryDirectory() as root:
            a=SharedChatReadPacer(root,interval=4,clock=lambda:clock[0],sleep=sleep)
            a.wait()
            b=SharedChatReadPacer(root,interval=4,clock=lambda:clock[0],sleep=sleep)
            b.wait();a.defer('12');b.wait()
        self.assertEqual(waits,[4,12])

    def test_persisted_cooldown_blocks_other_client_and_expires(self):
        import tempfile,os
        from chat_requests import record_rate_limit,cooldown_remaining,require_cloud_ready
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'LOCALAPPDATA':d}),patch('chat_requests.time.time',return_value=100):
            record_rate_limit('20');self.assertEqual(cooldown_remaining(),20)
            with self.assertRaisesRegex(Exception,'20'):require_cloud_ready()
            record_rate_limit('3');self.assertEqual(cooldown_remaining(),20)
            with patch('chat_requests.time.time',return_value=121):require_cloud_ready()

    def test_concurrent_processes_cannot_shorten_persisted_cooldown(self):
        import tempfile,subprocess,sys
        from chat_requests import cooldown_remaining
        with tempfile.TemporaryDirectory() as root:
            code='from chat_requests import record_rate_limit; import sys; record_rate_limit(sys.argv[1],sys.argv[2])'
            children=[subprocess.Popen([sys.executable,'-c',code,str(delay),root],creationflags=subprocess.CREATE_NO_WINDOW) for delay in (300,4,8)]
            self.assertTrue(all(p.wait(timeout=15)==0 for p in children))
            self.assertGreater(cooldown_remaining(root),280)

    def test_pause_set_during_pacing_blocks_archive_http(self):
        controller=Mock();controller.station.status.return_value={'phase':'idle'}
        with patch('archive_desktop.pace_chat_read'),patch('archive_desktop.require_cloud_ready',side_effect=[None,RuntimeError('pause')]):
            with self.assertRaisesRegex(RuntimeError,'pause'):ArchiveCloud(controller).request('get','/conversation/source')
        controller.cloud.http.get.assert_not_called()

    def test_reads_wait_and_retry_after_extends_the_pause(self):
        clock=[100];waits=[]
        def sleep(seconds):waits.append(seconds);clock[0]+=seconds
        pacer=ChatReadPacer(lambda:clock[0],sleep)
        pacer.wait();pacer.wait();pacer.defer('12');pacer.wait()
        self.assertEqual(waits,[4,12])

    def test_longer_existing_pause_is_never_shortened(self):
        clock=[100];pacer=ChatReadPacer(lambda:clock[0],Mock())
        pacer.defer('600');pacer.defer('3')
        self.assertEqual(pacer.next_read,700)

    def test_archive_transport_paces_reads_and_honors_server_delay(self):
        controller=Mock();response=controller.cloud.http.get.return_value
        controller.station.status.return_value={'phase':'idle'}
        response.status_code=429;response.headers={'Retry-After':'12'}
        with patch('archive_desktop.require_cloud_ready'),patch('archive_desktop.record_rate_limit'),patch('archive_desktop.pace_chat_read') as pace,patch('archive_desktop.defer_chat_reads') as defer:
            ArchiveCloud(controller).request('get','/conversation/source')
        pace.assert_called_once_with();defer.assert_called_once_with('12')

    def test_archive_waiting_for_pacer_rechecks_voice_before_http(self):
        controller=Mock();controller.station.status.side_effect=[{'phase':'idle'},{'phase':'active'}]
        with patch('archive_desktop.require_cloud_ready'),patch('archive_desktop.pace_chat_read'),self.assertRaisesRegex(Exception,'Voice is active'):
            ArchiveCloud(controller).request('get','/conversation/source')
        controller.cloud.http.get.assert_not_called()

    def test_active_voice_prevents_listing_and_history_requests(self):
        controller=Mock();controller.station.status.return_value={'phase':'active'}
        cloud=ArchiveCloud(controller)
        for read in (lambda:cloud.chats('project'),lambda:cloud.read('source')):
            with self.assertRaisesRegex(Exception,'Voice is active'):read()
        controller.cloud.chats.assert_not_called();controller.cloud.http.get.assert_not_called()
