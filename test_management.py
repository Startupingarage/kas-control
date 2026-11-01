import copy
import json
import unittest
from unittest.mock import Mock, patch

from backend import ControlError
from management import Manager

P = 'g-p-project'
CID = 'server-chat'
LID = '00000000-0000-4000-8000-000000000001'


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.control = Mock()
        self.station = self.control.station
        self.settings = {'chats': [], 'selected': '', 'voice': 'ember'}
        self.station.read_settings.side_effect = lambda: copy.deepcopy(self.settings)
        self.station.status.return_value = {'phase': 'active'}
        self.station.run.return_value = b'{"ok":true}'
        self.control.cloud.projects.return_value = [{'id': P, 'name': 'Project'}]
        self.control.cloud.chats.return_value = [{'id': CID, 'title': 'Cloud title'}]
        self.control.cloud.conversation.return_value = {'gizmo_id': P, 'current_node': 'parent', 'title': 'Cloud title'}
        self.doc = {'version': 1, 'routes': [], 'catalog': []}
        self.store = Mock()
        self.store.health.return_value = {'features': ['catalog', 'lazy_registration']}
        self.store.read.side_effect = lambda: copy.deepcopy(self.doc)
        self.store.write.side_effect = self.write
        self.store.native_commands.return_value = [(1, 'открой чат'), (3, 'закрой чат')]
        self.manager = Manager(self.control, self.store)

    def write(self, doc):
        self.doc = copy.deepcopy(doc)
        return doc

    def assert_no_voice(self):
        self.control.connect.assert_not_called()
        self.station.install.assert_not_called()
        self.station.command.assert_not_called()
        self.station.run.assert_not_called()

    def test_create_draft_only_writes_catalog(self):
        row = self.manager.create_chat(P, 'My title')
        self.assertTrue(row['draft'])
        self.assertEqual(row['title'], 'My title')
        self.assertEqual(self.doc['catalog'][0]['conversation_id'], '')
        self.assert_no_voice()
        self.control.cloud.conversation.assert_not_called()

    def test_create_with_command_is_one_catalog_transaction_without_voice(self):
        row=self.manager.create_chat(P,'Cooking','Алиса, открой готовку')
        self.assertEqual(row['command'],'открой готовку')
        self.assertEqual(self.doc['routes'][0]['chat_id'],row['local_id'])
        self.store.write.assert_called_once()
        self.assert_no_voice()

    def test_bad_initial_command_does_not_create_chat(self):
        for phrase in ('закрой чат','\ninvalid','x'):
            with self.assertRaises(ControlError):self.manager.create_chat(P,'Test',phrase)
        self.assertEqual(self.doc['catalog'],[]);self.store.write.assert_not_called()

    def test_duplicate_initial_command_and_full_routes_do_not_create_chat(self):
        row=self.manager.create_chat(P,'First','открой готовку')
        self.store.write.reset_mock()
        with self.assertRaises(ControlError):self.manager.create_chat(P,'Second','открой готовку')
        self.assertEqual(len(self.doc['catalog']),1);self.store.write.assert_not_called()
        self.doc['routes']=[self.doc['routes'][0]]*64
        with self.assertRaises(ControlError):self.manager.create_chat(P,'Second','открой новый')
        self.assertEqual(len(self.doc['catalog']),1);self.assert_no_voice()

    def test_assign_draft_does_not_activate(self):
        row = self.manager.create_chat(P, 'Draft')
        self.manager.save_command(P, row['key'], 'Алиса, открой работу')
        self.assertEqual(self.doc['routes'][0]['conversation_id'], '')
        self.assertEqual(self.doc['routes'][0]['phrase'], 'открой работу')
        self.assert_no_voice()

    def test_assign_cloud_verifies_and_registers_catalog_only(self):
        self.manager.save_command(P, 'cloud:' + CID, 'открой работу')
        entry = self.doc['catalog'][0]
        self.assertEqual(entry['conversation_id'], CID)
        self.assertEqual(entry['parent_message_id'], 'parent')
        self.assert_no_voice()

    def test_wrong_project_before_write(self):
        self.control.cloud.conversation.return_value['gizmo_id'] = 'g-p-other'
        with self.assertRaises(ControlError): self.manager.save_command(P, 'cloud:' + CID, 'работа')
        self.store.write.assert_not_called()
        self.assert_no_voice()

    def test_reserved_and_duplicate_rejected(self):
        row = self.manager.create_chat(P, 'Draft')
        with self.assertRaises(ControlError): self.manager.save_command(P, row['key'], 'закрой чат')
        self.manager.save_command(P, row['key'], 'работа')
        other = self.manager.create_chat(P, 'Other')
        with self.assertRaises(ControlError): self.manager.save_command(P, other['key'], 'работа')
        self.assertEqual(len(self.doc['routes']), 1)
        self.assert_no_voice()

    def test_realized_draft_keeps_identity_title_and_deduplicates(self):
        row = self.manager.create_chat(P, 'Chosen title')
        lid = row['local_id']
        self.settings['chats'] = [{'id': lid, 'gizmo_id': P, 'conversation_id': CID, 'parent_message_id': 'parent', 'title': 'Native title'}]
        self.settings['selected'] = lid
        self.control.cloud.conversation.side_effect = ControlError('Cloud not persisted yet')
        listed = self.manager.list_chats(P)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['title'], 'Chosen title')
        self.assertFalse(listed[0]['draft'])
        self.manager.connect_chat(P, row['key'])
        self.assertEqual(self.doc['catalog'][0]['chat_id'], lid)
        self.assertEqual(self.doc['catalog'][0]['conversation_id'], CID)
        self.station.run.assert_called_once_with('/data/opt/kas-command-router/router open ' + lid + ' || true', timeout=180)
        self.assertEqual(self.manager.bootstrap()['current']['title'], 'Chosen title')

    def test_existing_native_id_reused_for_cloud_key(self):
        self.settings['chats'] = [{'id': LID, 'gizmo_id': P, 'conversation_id': CID}]
        self.manager.save_command(P, 'cloud:' + CID, 'работа')
        self.assertEqual(self.doc['catalog'][0]['chat_id'], LID)
        self.assert_no_voice()

    def test_feature_gate_and_limits(self):
        self.store.health.return_value = {'features': []}
        with self.assertRaises(ControlError): self.manager.create_chat(P, 'Draft')
        self.store.write.assert_not_called()
        self.store.health.return_value = {'features': ['catalog', 'lazy_registration']}
        self.doc['catalog'] = [{}] * 64
        with self.assertRaises(ControlError): self.manager.create_chat(P, 'Draft')
        self.store.write.assert_not_called()

    def test_browser_draft_refused_saved_url_trusted(self):
        row = self.manager.create_chat(P, 'Draft')
        with patch('management.webbrowser.open', return_value=True) as browser:
            with self.assertRaises(ControlError): self.manager.open_in_chatgpt(P, row['key'])
            browser.assert_not_called()
            self.manager.open_in_chatgpt(P, 'cloud:' + CID)
            browser.assert_called_once_with('https://chatgpt.com/c/' + CID)

    def test_remove_route_preserves_draft_and_voice(self):
        row = self.manager.create_chat(P, 'Draft')
        self.manager.save_command(P, row['key'], 'работа')
        self.manager.remove_command(P, row['key'])
        self.assertEqual(self.doc['routes'], [])
        self.assertEqual(len(self.doc['catalog']), 1)
        self.assert_no_voice()

    def test_unsaved_realized_chat_does_not_open_browser(self):
        row = self.manager.create_chat(P, 'Draft')
        self.settings['chats'] = [{'id': row['local_id'], 'gizmo_id': P, 'conversation_id': CID}]
        self.control.cloud.conversation.side_effect = ControlError('Not saved')
        with patch('management.webbrowser.open') as browser:
            with self.assertRaises(ControlError): self.manager.open_in_chatgpt(P, row['key'])
            browser.assert_not_called()

    def test_close_waits_for_idle_without_repeating_command(self):
        self.station.status.side_effect = [{'phase': 'active'}, {'phase': 'stopping'}, {'phase': 'idle'}]
        with patch('management.time.sleep') as sleep:
            result = self.manager.close_session()
        self.assertEqual(result['phase'], 'idle')
        self.station.command.assert_called_once_with('close')
        self.assertEqual(sleep.call_count, 2)

    def test_close_timeout_never_repeats_command(self):
        with patch('management.time.monotonic', side_effect=[0, 0, 19]), patch('management.time.sleep'):
            with self.assertRaisesRegex(ControlError, 'не подтверждено'):
                self.manager.close_session()
        self.station.command.assert_called_once_with('close')
        self.station.status.assert_called_once()

    def test_close_readback_failure_is_uncertain(self):
        self.station.status.side_effect = ControlError('transport failure')
        with self.assertRaisesRegex(ControlError, 'Обновите состояние'):
            self.manager.close_session()
        self.station.command.assert_called_once_with('close')

    def test_router_error_preserves_recovery_outcome_without_raw_details(self):
        row = self.manager.create_chat(P, 'Draft')
        for error, expected in [
            ('activation failed; previous settings restored SECRET', 'автоматически восстановлена'),
            ('activation failed; recovery incomplete: SECRET', 'восстановление не завершено'),
            ('SECRET unknown error', 'не подтверждено')]:
            with self.subTest(error=error):
                self.station.run.return_value = json.dumps({'ok': False, 'error': error}).encode()
                with self.assertRaisesRegex(ControlError, expected) as caught:
                    self.manager.connect_chat(P, row['key'])
                self.assertNotIn('SECRET', str(caught.exception))

    def test_router_invalid_json_is_uncertain(self):
        row = self.manager.create_chat(P, 'Draft')
        self.station.run.return_value = b'not-json SECRET'
        with self.assertRaisesRegex(ControlError, 'не подтверждено') as caught:
            self.manager.connect_chat(P, row['key'])
        self.assertNotIn('SECRET', str(caught.exception))

    def test_router_ssh_failure_is_not_masked_by_shell(self):
        row = self.manager.create_chat(P, 'Draft')
        self.station.run.side_effect = ControlError('Нет связи с колонкой.')
        with self.assertRaisesRegex(ControlError, 'Нет связи'):
            self.manager.connect_chat(P, row['key'])

    def test_unspoken_native_cid_stays_prepared_until_cloud_lists_it(self):
        row = self.manager.create_chat(P, 'Prepared')
        self.settings['chats'] = [{'id': row['local_id'], 'gizmo_id': P,
                                   'conversation_id': CID, 'parent_message_id': ''}]
        self.control.cloud.chats.return_value = []
        self.assertTrue(self.manager.list_chats(P)[0]['draft'])
        self.control.cloud.chats.return_value = [{'id': CID, 'title': 'Saved'}]
        self.assertFalse(self.manager.list_chats(P)[0]['draft'])

    def test_catalog_only_unspoken_identity_reopens_without_cloud_get(self):
        self.doc['catalog'] = [{'chat_id': LID, 'project_id': P, 'title': 'Prepared',
                                'conversation_id': CID, 'parent_message_id': ''}]
        self.control.cloud.conversation.side_effect = ControlError('Not saved')
        self.manager.connect_chat(P, 'local:' + LID)
        self.control.cloud.conversation.assert_not_called()
        self.assertEqual(self.doc['catalog'][0]['chat_id'], LID)
        self.assertEqual(self.doc['catalog'][0]['conversation_id'], CID)
        self.assertEqual(self.doc['catalog'][0]['parent_message_id'], '')
        self.station.run.assert_called_once_with('/data/opt/kas-command-router/router open ' + LID + ' || true', timeout=180)

    def test_unspoken_catalog_identity_cannot_cross_project(self):
        self.doc['catalog'] = [{'chat_id': LID, 'project_id': 'g-p-other', 'title': 'Prepared',
                                'conversation_id': CID, 'parent_message_id': ''}]
        with self.assertRaises(ControlError): self.manager.connect_chat(P, 'local:' + LID)
        self.station.run.assert_not_called()
        self.store.write.assert_not_called()


if __name__ == '__main__': unittest.main()
