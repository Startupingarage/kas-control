import unittest
from unittest.mock import Mock, patch
from backend import ControlError
from routes import normalize_phrase, update_routes, merge_chats, RouteStore
from test_backend import FakeStation, FakeCloud, PROJECT, CID, OTHER
from backend import Controller

class RouteTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_phrase(' Яндекс, открой Кулинарный чат! '),'открой кулинарный чат')
        self.assertEqual(normalize_phrase('Алиса, открой ещё чат, пожалуйста'),'открой еще чат')

    def test_collision_does_not_overwrite_other_chat(self):
        old={'version':1,'routes':[dict(phrase='открой чат',chat_id='a',project_id='g-p-a',conversation_id='cid')]}
        with self.assertRaises(ControlError):
            update_routes(old,'Алиса, открой чат',dict(id='b',gizmo_id='g-p-b',conversation_id='other'),[], 'B')
        self.assertEqual(old['routes'][0]['chat_id'],'a')

    def test_reserved_close_rejected(self):
        with self.assertRaises(ControlError):
            update_routes({'version':1,'routes':[]},'закрой чат',dict(id='a',gizmo_id='g-p-a',conversation_id='cid'),[(3,'закрой чат')],'A')

    def test_replace_own_binding_preserves_others(self):
        old={'version':1,'routes':[dict(phrase='старое',chat_id='a'),dict(phrase='другое',chat_id='b')]}
        new=update_routes(old,'открой рабочий чат',dict(id='a',gizmo_id='g-p-a',conversation_id='cid',title='Работа'),[],'Core')
        self.assertEqual(len(new['routes']),2)
        self.assertEqual(new['routes'][0]['chat_id'],'b')
        self.assertEqual(new['routes'][1]['phrase'],'открой рабочий чат')
        self.assertEqual(old['routes'][0]['phrase'],'старое')

    def test_pending_local_chat_visible_without_cloud_duplicate(self):
        cloud=[{'id':'saved','title':'Saved'}]
        local=[dict(id='local1',conversation_id='saved',gizmo_id='g-p-a',title='Local'),dict(id='local2',conversation_id='pending',gizmo_id='g-p-a',title='New')]
        rows=merge_chats(cloud,local,'g-p-a')
        self.assertEqual(len(rows),2)
        self.assertTrue(rows[1]['pending'])
        self.assertEqual(rows[0]['title'],'Saved')

class AssignmentTests(unittest.TestCase):
    def setUp(self):
        self.station=FakeStation();self.cloud=FakeCloud();self.control=Controller(self.station,self.cloud)
        self.store=RouteStore(self.station)
        self.store.read=Mock(return_value={'version':1,'routes':[]})
        self.store.native_commands=Mock(return_value=[(3,'закрой чат')])
        self.store.health=Mock(return_value={'ready':True})
        self.store.write=Mock()

    def test_registered_chat_assign_does_not_reconnect_voice(self):
        self.store.assign(self.control,PROJECT,CID,'открой чат')
        self.assertEqual(self.station.actions,[])
        self.assertEqual(self.store.write.call_args.args[0]['routes'][0]['chat_id'],'local-old')

    def test_reserved_command_does_not_register_new_chat(self):
        with self.assertRaises(ControlError):self.store.assign(self.control,PROJECT,OTHER,'закрой чат')
        self.assertEqual(self.station.actions,[]);self.store.write.assert_not_called()

    def test_wrong_project_does_not_write_or_reconnect(self):
        self.cloud.conversation=lambda _: {'id':CID,'gizmo_id':'g-p-other'}
        with self.assertRaises(ControlError):self.store.assign(self.control,PROJECT,CID,'открой чат')
        self.assertEqual(self.station.actions,[]);self.store.write.assert_not_called()

    def test_offline_router_does_not_register_chat(self):
        self.store.health.side_effect=ControlError('offline')
        with self.assertRaises(ControlError):self.store.assign(self.control,PROJECT,OTHER,'открой рабочий чат')
        self.assertEqual(self.station.actions,[])

    def test_first_assignment_registers_target_before_config(self):
        self.store.assign(self.control,PROJECT,OTHER,'открой рабочий чат')
        self.assertEqual(self.station.actions,['install'])
        route=self.store.write.call_args.args[0]['routes'][0]
        self.assertEqual(route['chat_id'],self.station.doc['selected'])
        self.assertEqual(self.station.doc['voice'],'orbit')

    def test_missing_ack_attempts_guarded_restore_without_retrying_save(self):
        store=RouteStore(Mock());store.hash='a'*64;doc={'version':1,'routes':[]}
        store.read=Mock(return_value=doc);store.health=Mock(side_effect=ControlError('offline'))
        with patch('routes.time.sleep'),self.assertRaises(ControlError):store.write(doc)
        self.assertEqual(store.station.run.call_count,2)
        restore=store.station.run.call_args.args[0]
        self.assertIn('KAS_ERROR:STALE',restore)
        self.assertIn('routes.previous.json',restore)

if __name__=='__main__': unittest.main()
