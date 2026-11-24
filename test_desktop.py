import tempfile
import unittest
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from backend import ControlError
from desktop import Bridge, StaticUI, same_page

class DesktopTests(unittest.TestCase):
    def bridge(self):
        manager=Mock();manager.bootstrap.return_value={'projects':[]}
        bridge=Bridge(manager)
        bridge._window=SimpleNamespace(_server=SimpleNamespace(address='http://127.0.0.1:50123/'),
                                       get_current_url=lambda:'http://127.0.0.1:50123/')
        return bridge,manager

    def test_only_same_local_page_can_use_bridge(self):
        bridge,manager=self.bridge()
        bridge._window.get_current_url=lambda:'https://evil.example/'
        self.assertFalse(bridge.bootstrap()['ok']);manager.bootstrap.assert_not_called()
        for target in ('http://127.0.0.1:50124/','http://localhost:50123/','http://127.0.0.1:50123/assets/app.js'):
            self.assertFalse(same_page(target,'http://127.0.0.1:50123/'))

    def test_busy_rejects_parallel_requests_and_close(self):
        bridge,manager=self.bridge();bridge._lock.acquire()
        try:
            self.assertFalse(bridge.bootstrap()['ok']);self.assertFalse(bridge._can_close())
            manager.bootstrap.assert_not_called()
        finally:bridge._lock.release()

    def test_ui_close_is_completed_after_inflight_management_finishes(self):
        bridge,_=self.bridge();closed=threading.Event()
        bridge._window.events=SimpleNamespace(closed=closed)
        bridge._window.destroy=Mock(side_effect=closed.set)
        bridge._lock.acquire()
        try:
            self.assertFalse(bridge._close_ui());bridge._window.destroy.assert_not_called()
        finally:bridge._lock.release()
        self.assertTrue(closed.wait(2));bridge._window.destroy.assert_called_once()

    def test_internal_errors_do_not_expose_details(self):
        bridge,manager=self.bridge();manager.bootstrap.side_effect=RuntimeError('secret-token')
        result=bridge.bootstrap();self.assertFalse(result['ok']);self.assertNotIn('secret-token',result['error'])
        self.assertEqual(bridge._last_error['type'],'RuntimeError')
        self.assertNotIn('secret-token',str(bridge._last_error))
        self.assertTrue(bridge._can_close())

    def test_success_returns_only_manager_result(self):
        bridge,manager=self.bridge();self.assertEqual(bridge.bootstrap(),{'ok':True,'data':{'projects':[]}})

    def test_cloud_catalog_lock_does_not_block_local_state(self):
        bridge,manager=self.bridge();manager.bootstrap_local.return_value={'projects':[]}
        bridge._catalog_lock.acquire()
        try:
            self.assertTrue(bridge.bootstrap_local()['ok'])
            self.assertFalse(bridge.cloud_projects()['ok'])
        finally:bridge._catalog_lock.release()

    def test_catalog_auth_fallback_is_cancelled_after_ui_closed(self):
        bridge,manager=self.bridge();bridge._auto_login=True;bridge._ui_closed.set()
        manager.cloud_projects.side_effect=ControlError('ChatGPT отклонил доступ к списку.')
        with patch('browser_login.BrowserSession') as browser:
            self.assertFalse(bridge.cloud_projects()['ok']);browser.assert_not_called()

    def test_archive_poll_is_local_and_does_not_login_or_bootstrap(self):
        bridge,manager=self.bridge();bridge._auto_login=True
        bridge._archive=Mock(working=False,started_at=0)
        bridge._archive_hourly=False
        manager.archive_status.return_value={'available':True,'updated':1}
        bridge._lock.acquire()
        try:
            result=bridge.archive_status()
            self.assertTrue(result['ok']);self.assertFalse(result['data']['running'])
            self.assertFalse(result['data']['available']);manager.bootstrap.assert_not_called()
        finally:bridge._lock.release()
        bridge._window.get_current_url=lambda:'https://evil.example/'
        self.assertFalse(bridge.archive_status()['ok'])

    def test_disposable_ui_reads_tray_timer_failure(self):
        bridge,_=self.bridge();bridge._archive=Mock(working=False,started_at=0)
        bridge._archive_hourly=True
        with patch('archive_schedule.timer_error',return_value='cannot start worker'):
            self.assertEqual(bridge._archive_info({})['automatic_error'],'cannot start worker')

    def test_bootstrap_resumes_saved_browser_session(self):
        bridge,manager=self.bridge();bridge._auto_login=True;browser=Mock();browser.authorized=False
        browser.closed.is_set.return_value=False;bridge._browser=browser
        self.assertTrue(bridge.bootstrap()['ok'])
        browser.login.assert_called_once_with(interactive=False)
        self.assertIs(manager.cloud.http,browser)

    def test_static_server_has_no_api_or_directory_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'ui';root.mkdir();(root/'index.html').write_text('<head></head><body>UI</body>')
            (Path(folder)/'secret').write_text('secret')
            app=StaticUI(root);status=[]
            def start(code,headers):status.append(code)
            for path,method in [('/../secret','GET'),('/js_api/123','POST'),('/','POST')]:
                body=b''.join(app({'PATH_INFO':path,'REQUEST_METHOD':method},start))
                self.assertNotIn(b'secret',body);self.assertNotIn(status[-1],['200 OK'])
            body=b''.join(app({'PATH_INFO':'/','REQUEST_METHOD':'GET'},start))
            self.assertEqual(status[-1],'200 OK');self.assertIn(b'UI',body)

if __name__=='__main__':unittest.main()
