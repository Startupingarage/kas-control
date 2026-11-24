import os,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from archive_schedule import AppArchiveTimer,enabled,set_enabled
from backend import ControlError
from tray_app import SingleInstance

class ScheduleTests(unittest.TestCase):
    def test_due_and_missed_runs_start_once_and_persist_next_deadline(self):
        from archive_schedule import preference_path
        import json
        clock=[100.0];archive=Mock();timer=AppArchiveTimer(archive);timer.next_run_at=200
        waits=[]
        def wait(seconds):
            waits.append(seconds)
            if len(waits)==1:clock[0]=200
            elif len(waits)==2:clock[0]+=7*43200
            else:timer.stopped.set()
            return False
        timer.changed.wait=Mock(side_effect=wait)
        with patch('archive_schedule.time.time',side_effect=lambda:clock[0]),patch('archive_schedule.enabled',return_value=True):
            timer.run()
        self.assertEqual(archive.start.call_count,2)
        self.assertTrue(all(seconds<=60 for seconds in waits))
        saved=json.loads(preference_path().with_name('archive-timer.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['next_run_at'],clock[0]+43200)
        self.assertEqual(AppArchiveTimer(Mock()).next_run_at,saved['next_run_at'])

    def test_invalid_persisted_deadline_cannot_kill_or_spin_timer(self):
        import json,math
        from archive_schedule import preference_path
        path=preference_path().with_name('archive-timer.json');path.parent.mkdir(parents=True)
        for value in (float('nan'),float('inf'),True,'tomorrow'):
            path.write_text(json.dumps({'interval_seconds':43200,'next_run_at':value}),encoding='utf-8')
            timer=AppArchiveTimer(Mock());self.assertTrue(math.isfinite(timer.next_run_at))
            self.assertGreater(timer.next_run_at,43200)

    def test_default_is_twelve_hours_and_next_run_survives_restart(self):
        import json
        from archive_schedule import preference_path
        first=AppArchiveTimer(Mock());self.assertEqual(first.interval,43200)
        first.next_run_at=1234567890;first._publish_error()
        self.assertEqual(AppArchiveTimer(Mock()).next_run_at,1234567890)
        self.assertTrue(set_enabled('twice_daily'))
        self.assertTrue(enabled())

    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        env=patch.dict(os.environ,{'LOCALAPPDATA':self.folder.name});env.start();self.addCleanup(env.stop)

    def test_timer_failure_is_available_to_a_disposable_ui_and_restart_clears_it(self):
        from archive_schedule import timer_error
        archive=Mock();seen=threading.Event()
        def fail():seen.set();raise RuntimeError('cannot start worker')
        archive.start.side_effect=fail
        with patch('archive_schedule.enabled',return_value=True):
            timer=AppArchiveTimer(archive,interval=.02);timer.start()
            self.assertTrue(seen.wait(2));timer.stop()
        self.assertEqual(timer_error(),'cannot start worker')
        next_timer=AppArchiveTimer(archive,interval=3600);next_timer.start();next_timer.stop()
        self.assertEqual(timer_error(),'')

    def test_invalid_timer_status_does_not_break_the_ui(self):
        from archive_schedule import timer_error,preference_path
        path=preference_path().with_name('archive-timer.json');path.parent.mkdir(parents=True)
        for value in ('broken','[]','{"automatic_error":3}'):
            path.write_text(value,encoding='utf-8');self.assertEqual(timer_error(),'')

    def test_unwritable_timer_status_does_not_disable_hourly_runs(self):
        archive=Mock();seen=threading.Event();archive.start.side_effect=seen.set
        with patch('archive_schedule.enabled',return_value=True),patch('archive_schedule.os.replace',side_effect=PermissionError):
            timer=AppArchiveTimer(archive,interval=.02);timer.start()
            self.assertTrue(seen.wait(2));timer.stop()

    def test_second_instance_signals_the_first_instead_of_owning_another_app(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'USERPROFILE':d}):
            first=SingleInstance();second=SingleInstance();shown=threading.Event()
            try:
                self.assertFalse(first.existing);self.assertTrue(second.existing)
                first.listen(shown.set);second.notify();self.assertTrue(shown.wait(2))
            finally:second.close();first.close()
    def test_mode_is_local_and_persistent_without_any_scheduler_process(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'LOCALAPPDATA':d}):
            self.assertFalse(enabled())
            self.assertTrue(set_enabled('hourly'))
            self.assertTrue(enabled())
            self.assertFalse(set_enabled('manual'))
            with self.assertRaises(ControlError):set_enabled('unknown')

    def test_enabled_timer_starts_existing_worker_and_exit_stops_future_starts(self):
        archive=Mock();started=threading.Event();archive.start.side_effect=started.set
        with patch('archive_schedule.enabled',return_value=True):
            timer=AppArchiveTimer(archive,interval=.03);timer.start()
            self.assertTrue(started.wait(2));timer.stop()
            self.assertFalse(timer.thread.is_alive());calls=archive.start.call_count
            timer.changed.wait(.08);self.assertEqual(archive.start.call_count,calls)

    def test_manual_mode_does_not_launch_archive(self):
        archive=Mock()
        with patch('archive_schedule.enabled',return_value=False):
            timer=AppArchiveTimer(archive,interval=.01);timer.start()
            threading.Event().wait(.05);timer.stop();archive.start.assert_not_called()

    def test_worker_failure_does_not_kill_the_timer(self):
        archive=Mock();seen=threading.Event()
        def fail():seen.set();raise RuntimeError('cannot start worker')
        archive.start.side_effect=fail
        with patch('archive_schedule.enabled',return_value=True):
            timer=AppArchiveTimer(archive,interval=.02);timer.start()
            self.assertTrue(seen.wait(2));timer.stop();self.assertEqual(timer.last_error,'cannot start worker')
