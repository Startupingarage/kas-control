import os,sys,tempfile,subprocess,unittest,threading
from pathlib import Path
from unittest.mock import Mock,patch
from archive_control import ArchiveControl
from backend import SSH

class ArchiveControlTests(unittest.TestCase):
    def test_stop_waits_for_inflight_start_before_stopping_its_child(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'LOCALAPPDATA':d}),patch('archive_control.subprocess.Popen') as popen:
            spawning=threading.Event();release=threading.Event();stopped=threading.Event()
            child=Mock();child.poll.return_value=None
            def spawn(*args,**kwargs):spawning.set();release.wait(2);return child
            popen.side_effect=spawn;control=ArchiveControl()
            starter=threading.Thread(target=control.start);starter.start()
            self.assertTrue(spawning.wait(2))
            stopper=threading.Thread(target=lambda:(control.stop(),stopped.set()));stopper.start()
            try:self.assertFalse(stopped.wait(.05))
            finally:release.set();starter.join(2);stopper.join(2)
            self.assertTrue(stopped.is_set());child.wait.assert_called_once()
            self.assertFalse(control.running)

    def test_native_ssh_never_boots_wsl(self):
        self.assertTrue(SSH[0].lower().endswith('ssh.exe'))
        self.assertFalse(any('wsl' in a.lower() or 'stationlab' in a.lower() for a in SSH))

    def test_manual_start_stop_keeps_receipts_and_owns_only_its_process(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'LOCALAPPDATA':d}),patch('archive_control.subprocess.Popen') as popen:
            child=popen.return_value;child.pid=456;child.poll.return_value=None
            control=ArchiveControl();control.root.mkdir(parents=True)
            receipt=control.root/'receipt.json';receipt.write_text('keep')
            (control.root/'stop').touch();control.start()
            args=popen.call_args.args[0]
            self.assertIn('--once',args);self.assertEqual(args[args.index('--owner-pid')+1],str(os.getpid()))
            self.assertFalse((control.root/'stop').exists());self.assertTrue(control.running)
            control.stop();self.assertFalse(control.running)
            self.assertTrue((control.root/'stop').exists());self.assertEqual(receipt.read_text(),'keep')

    def test_stuck_worker_tree_can_be_stopped_without_touching_other_processes(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'LOCALAPPDATA':d}),patch('archive_control.subprocess.run') as kill:
            c=ArchiveControl();c.root.mkdir(parents=True);c.process=Mock(pid=789)
            c.process.poll.return_value=None;c.process.wait.side_effect=subprocess.TimeoutExpired('worker',3)
            c.stop();self.assertEqual(kill.call_args.args[0],['taskkill.exe','/PID','789','/T','/F'])

    def test_closing_a_window_that_did_not_start_archive_does_nothing(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'LOCALAPPDATA':d}):
            c=ArchiveControl();c.stop();self.assertFalse(c.root.exists())

if __name__=='__main__':unittest.main()
