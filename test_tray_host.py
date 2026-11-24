import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from tray_host import TrayHost,TrayArchiveClient,OPEN,START,STOP

class TrayHostTests(unittest.TestCase):
    def host(self):
        host=TrayHost();host.user=Mock();host.archive=Mock();host.timer=Mock();host.window=42
        return host

    def test_repeated_open_does_not_create_a_second_ui(self):
        host=self.host();host.ui=Mock();host.ui.poll.return_value=None
        with patch('tray_host.subprocess.Popen') as spawn:host.open_ui();spawn.assert_not_called()
        host.user.ShowWindow.assert_called_once()

    def test_closed_ui_is_replaced_on_next_open_without_loading_webview(self):
        host=self.host();host.ui=Mock();host.ui.poll.return_value=0
        with patch('tray_host.subprocess.Popen') as spawn:
            host.open_ui();self.assertIn('--ui-child',spawn.call_args.args[0])
        host.archive.start.assert_not_called()

    def test_manual_requests_are_owned_by_tray_not_ui(self):
        host=self.host()
        self.assertEqual(host.dispatch(42,START,0,0),1)
        self.assertEqual(host.dispatch(42,STOP,0,0),1)
        host.archive.start.assert_called_once();host.archive.request_stop.assert_called_once()

    def test_archive_cannot_start_after_exit_has_begun(self):
        host=self.host();host.exiting=True
        self.assertEqual(host.dispatch(42,START,0,0),0)
        host.archive.start.assert_not_called()

    def test_exit_stops_timer_worker_and_ui_and_is_idempotent(self):
        host=self.host();host.ui=Mock();host.ui.poll.return_value=None
        host.exit();host.exit()
        host.timer.stop.assert_called_once();host.archive.stop.assert_called_once()
        host.ui.wait.assert_called_once();host.user.PostMessageW.assert_any_call(42,0x10,0,0)

    def test_unresponsive_ui_cleanup_targets_only_owned_tree(self):
        host=self.host();host.ui=Mock(pid=12345);host.ui.poll.return_value=None
        host.ui.wait.side_effect=subprocess.TimeoutExpired('ui',10)
        with patch('tray_host.subprocess.run') as kill:
            host.exit();self.assertEqual(kill.call_args.args[0],['taskkill.exe','/PID','12345','/T','/F'])

    def test_ui_proxy_never_starts_a_worker_in_its_own_process(self):
        with patch.object(TrayArchiveClient,'_send') as send:
            proxy=TrayArchiveClient();proxy.start();proxy.request_stop()
            self.assertEqual([c.args for c in send.call_args_list],[(START,),(STOP,)])
