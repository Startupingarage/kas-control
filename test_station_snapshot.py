import hashlib,json,unittest
from unittest.mock import Mock
from backend import Station,ControlError

class StationSnapshotTests(unittest.TestCase):
    def test_one_ssh_read_returns_consistent_management_state(self):
        station=Station();station.run=Mock()
        settings={'chats':[],'selected':'','voice':'orbit'}
        revision=hashlib.sha256(json.dumps(settings,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        def answer(script):
            marker=next(x.split("'",2)[1] for x in script.splitlines() if x.startswith('M='))
            values=['100',json.dumps(settings),json.dumps({'pid':2230,'settings_revision':revision}),'{"version":1,"routes":[]}','1\tоткрой чат','{"ready":true,"connected":true,"updated_unix":99}', 'a'*64,'b'*64]
            return ('\n'+marker+'\n'+('\n'+marker+'\n').join(values)+'\n'+marker+'\n').encode()
        station.run.side_effect=answer
        snap=station.management_snapshot()
        self.assertEqual(snap['settings'],settings);self.assertEqual(snap['state']['pid'],2230)
        self.assertEqual(snap['commands'],[(1,'открой чат')]);station.run.assert_called_once()
        self.assertEqual(station.last_settings_hash,'a'*64)

        def changed_once(script):
            if station.run.call_count==1:
                raise ControlError('Настройки изменились во время подготовки. Обновите список и повторите.')
            return answer(script)
        station.run.reset_mock();station.run.side_effect=changed_once
        self.assertEqual(station.management_snapshot()['state']['pid'],2230)
        self.assertEqual(station.run.call_count,2)

    def test_malformed_snapshot_is_rejected(self):
        station=Station();station.run=Mock(return_value=b'broken')
        with self.assertRaises(ControlError):station.management_snapshot()
