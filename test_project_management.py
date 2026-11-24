from test_management import ManagementTests, P
from backend import ControlError

class ProjectManagementTests(ManagementTests):
    def test_local_bootstrap_does_not_access_cloud(self):
        self.doc.update(project_sessions=True,catalog=[{'chat_id':'local','project_id':P,'title':'Kitchen','project_name':'Kitchen'}],routes=[{'chat_id':'local','project_id':P,'phrase':'готовка'}])
        result=self.manager.bootstrap_local()
        self.assertEqual(result['projects'],[{'id':P,'name':'Kitchen'}])
        self.station.management_snapshot.assert_called_once();self.control.cloud.projects.assert_not_called()

    def test_created_project_appears_in_next_local_bootstrap(self):
        from unittest.mock import patch
        with patch('management.create_project',return_value={'id':'g-p-new','name':'New project'}):
            self.manager.create_project('New project')
        self.assertIn({'id':'g-p-new','name':'New project'},self.manager.bootstrap_local()['projects'])

    def test_project_open_does_not_need_cloud_catalog(self):
        self.doc.update(project_sessions=True,routes=[{'project_id':P,'chat_id':'local','phrase':'готовка'}])
        self.manager.open_project(P);self.control.cloud.projects.assert_not_called()

    def test_recovery_preserves_old_chat_and_voice_and_rejects_uncertain_result(self):
        old={'id':'old','gizmo_id':P,'conversation_id':'prior'}
        before={'settings':{'selected':'old','voice':'orbit','chats':[old]},'state':{'pid':2230,'phase':'active'},'routes':{'project_sessions':True}}
        after={'settings':{'selected':'new','voice':'orbit','chats':[old,{'id':'new','gizmo_id':P,'conversation_id':'fresh'}]},'state':{'pid':2230,'phase':'active'}}
        from unittest.mock import patch
        for valid in (True,False):
            if not valid:after['settings']['voice']='ember'
            self.station.management_snapshot.side_effect=[before,after]
            with patch.object(self.manager,'open_project') as open_project:
                if valid:self.assertTrue(self.manager.reconnect_project(P)['previous_preserved'])
                else:
                    with self.assertRaises(ControlError):self.manager.reconnect_project(P)
                open_project.assert_called_once_with(P)

    def test_ordinary_copy_switch_is_local_project_scoped_and_never_touches_voice(self):
        import tempfile,os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'LOCALAPPDATA':d}):
            self.assertTrue(self.manager.set_ordinary_archive(P,'enabled')['ordinary_projects'][P])
            self.assertTrue(self.manager.archive_status()['ordinary_projects'][P])
            self.assertFalse(self.manager.set_ordinary_archive(P,'disabled')['ordinary_projects'][P])
            with self.assertRaises(ControlError):self.manager.set_ordinary_archive('missing','enabled')
            self.assert_no_voice()
    def test_archive_queue_titles_are_read_as_utf8(self):
        import tempfile,os,time,json
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'LOCALAPPDATA':d}):
            path=Path(d)/'KasControl/Archive/status.json';path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'completed_at':time.time(),'queue':[{'title':'Готовка и учеба'}]},ensure_ascii=False),encoding='utf-8')
            self.doc['project_sessions']=True
            result=self.manager.project_status(self.settings,{},self.doc)
            self.assertEqual(result['archive']['items'][0]['title'],'Готовка и учеба')

    def test_project_command_preserves_slot_and_never_touches_voice(self):
        self.doc['project_sessions']=True
        self.manager.save_project_command(P,'Алиса, готовка')
        first=self.doc['routes'][0]['chat_id']
        self.manager.save_project_command(P,'кухня')
        self.assertEqual(first,self.doc['routes'][0]['chat_id'])
        self.assertEqual(self.doc['routes'][0]['phrase'],'кухня')
        self.assertTrue(self.doc['catalog'][0]['project_session'])
        self.assert_no_voice()

    def test_reserved_and_last_project_are_protected(self):
        self.doc['project_sessions']=True
        with self.assertRaises(ControlError):self.manager.save_project_command(P,'открой чат')
        self.manager.save_project_command(P,'готовка')
        with self.assertRaises(ControlError):self.manager.remove_project_command(P)
        self.assertEqual(len(self.doc['routes']),1)
        self.assert_no_voice()

    def test_project_mode_rejects_legacy_mutations(self):
        self.doc['project_sessions']=True
        for method,args in [('create_chat',(P,'Test')),('save_command',(P,'local:x','test')),('remove_command',(P,'local:x')),('connect_chat',(P,'local:x')),('restore_binding',())]:
            with self.assertRaises(ControlError):getattr(self.manager,method)(*args)
        self.assert_no_voice()

    def test_open_uses_router_without_writing_configuration(self):
        self.doc['project_sessions']=True
        self.manager.save_project_command(P,'готовка');self.store.write.reset_mock()
        self.manager.open_project(P)
        self.store.write.assert_not_called()
        self.assertIn('/router open ',self.station.run.call_args.args[0])
        self.station.install.assert_not_called()
