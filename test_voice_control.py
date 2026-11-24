import unittest
from unittest.mock import Mock

from backend import ControlError, Station
from test_desktop import DesktopTests
from test_management import ManagementTests


class VoiceStationTests(unittest.TestCase):
    def station(self, voice='orbit'):
        station = Station()
        station.run = Mock(return_value=b'{"ok":true}')
        station.status = Mock(side_effect=[
            {'pid':2228, 'phase':'active', 'settings':{'voice':'orbit'}},
            {'pid':2228, 'phase':'active', 'settings':{'voice':voice}},
        ])
        station.read_settings = Mock(return_value={'voice':voice})
        return station

    def test_saves_with_native_command_without_restarting_or_opening(self):
        station = self.station('ember')
        result = station.set_voice('ember')
        self.assertEqual(result['voice'], 'ember')
        self.assertEqual(result['previous_voice'], 'orbit')
        script = station.run.call_args.args[0]
        self.assertIn(' voice ', script)
        self.assertIn('rollback.sh', script)
        self.assertNotIn('settings.json', script)
        for forbidden in ('kill', 'restart', ' open ', ' close ', 'runtime.env'):
            self.assertNotIn(forbidden, script)

    def test_current_voice_is_noop(self):
        station = self.station()
        self.assertEqual(station.set_voice('orbit')['voice'], 'orbit')
        station.run.assert_not_called()

    def test_invalid_input_never_reaches_column(self):
        for voice in ('', 'spruce', 'bad;reboot', None, [], 'astra'):
            station = self.station()
            with self.assertRaises(ControlError):station.set_voice(voice)
            station.status.assert_not_called()
            station.run.assert_not_called()

    def test_rejects_failed_or_unconfirmed_result(self):
        station = self.station('ember')
        station.run.return_value=b'{"ok":false}'
        with self.assertRaises(ControlError):station.set_voice('ember')
        station = self.station('orbit')
        with self.assertRaises(ControlError):station.set_voice('ember')
        station = self.station('ember')
        station.read_settings.return_value={'voice':'orbit'}
        with self.assertRaises(ControlError):station.set_voice('ember')

    def test_process_change_is_not_silently_accepted(self):
        station = self.station('ember')
        station.status.side_effect=[{'pid':2228,'settings':{'voice':'orbit'}},
                                    {'pid':9999,'settings':{'voice':'ember'}}]
        with self.assertRaises(ControlError):station.set_voice('ember')


class VoiceManagerTests(ManagementTests):
    def test_voice_does_not_require_cloud_or_router(self):
        self.station.set_voice.return_value={'voice':'ember','previous_voice':'orbit'}
        self.assertEqual(self.manager.set_voice('ember')['voice'],'ember')
        self.station.set_voice.assert_called_once_with('ember')
        self.cloud_not_used()

    def cloud_not_used(self):
        self.control.cloud.projects.assert_not_called()
        self.store.read.assert_not_called()
        self.station.install.assert_not_called()
        self.station.command.assert_not_called()


class VoiceBridgeTests(DesktopTests):
    def test_voice_uses_origin_and_serialization_guard(self):
        bridge, manager=self.bridge()
        manager.set_voice.return_value={'voice':'ember'}
        self.assertEqual(bridge.set_voice('ember'),{'ok':True,'data':{'voice':'ember'}})
        bridge._lock.acquire()
        try:self.assertFalse(bridge.set_voice('ember')['ok'])
        finally:bridge._lock.release()
        bridge._window.get_current_url=lambda:'https://evil.example/'
        self.assertFalse(bridge.set_voice('ember')['ok'])
        manager.set_voice.assert_called_once_with('ember')
