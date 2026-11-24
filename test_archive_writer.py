import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from archive_desktop import BrowserWriter
from archive_worker import NotSubmitted
from test_voice_archive import destination

class WriterTests(unittest.TestCase):
    def test_progress_keeps_voice_waiting_state(self):
        import json
        from archive_desktop import progress_status
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);report={'voice_phase':'active','note':'voice_active'}
            progress_status(root,report)
            self.assertEqual(json.loads((root/'status.json').read_text())['note'],'voice_active')

    def test_voice_start_during_page_load_stops_before_auth_or_send(self):
        window=Mock();controller=Mock();cloud=Mock()
        controller.station.run.return_value='{"access_token":"fixture"}'
        controller.station.status.side_effect=[{'phase':'idle'},{'phase':'idle'},{'phase':'active'}]
        cloud.read.return_value={'gizmo_id':'project'}
        window.evaluate_js.return_value={'host':'chatgpt.com','path':'/g/project/c/archive','count':0}
        clock=iter([0,0,50])
        with tempfile.TemporaryDirectory() as d,patch('archive_desktop.identity',return_value='account'),patch('archive_desktop.pace_chat_read'),patch('archive_desktop.require_cloud_ready'),patch('archive_desktop.time.monotonic',side_effect=lambda:next(clock)),patch('archive_desktop.time.sleep'):
            writer=BrowserWriter(window,controller,cloud,Path(d))
            with self.assertRaisesRegex(NotSubmitted,'Voice is active'):writer('project','archive','text')
        window.evaluate_js.assert_not_called()
        window.load_url.assert_called_with('about:blank')

    def test_active_voice_blocks_writer_before_browser_navigation(self):
        window=Mock();controller=Mock();controller.station.status.return_value={'phase':'active'}
        with tempfile.TemporaryDirectory() as d:
            writer=BrowserWriter(window,controller,Mock(),Path(d))
            with self.assertRaises(NotSubmitted):writer('project','archive','text')
        window.load_url.assert_called_once_with('about:blank')
        window.evaluate_js.assert_not_called()

    def exercise(self,draft='',confirmed=True,voice_started=False):
        window=Mock();controller=Mock();controller.station.run.return_value=b'{"access_token":"fixture"}'
        controller.station.status.return_value={'phase':'idle'}
        if voice_started:controller.station.status.side_effect=[{'phase':'idle'},{'phase':'active'}]
        cloud=Mock();text='Synthetic archive payload';state={'draft':draft,'sent':False,'cleared':False,'readback':False}
        def read(cid):
            if state['sent'] and confirmed:state['readback']=True;return destination(text)
            return {'conversation_id':'archive','gizmo_id':'project'}
        cloud.read.side_effect=read
        def evaluate(script,*args):
            if script.startswith('({host:'):return {'host':'chatgpt.com','path':'/g/project/c/archive','count':1}
            if script.endswith('.trim()'):return state['draft']
            if script.startswith('({text:'):return {'text':state['draft'],'buttons':1}
            if 'b[0].click()' in script:state['sent']=True;return True
            if script=='location.pathname':return '/g/project/c/archive'
            if "document.execCommand('delete')" in script:
                self.assertTrue(state['readback']);state['cleared']=True;return True
            if "execCommand('insertText'" in script:state['draft']=text;return True
            return None
        window.evaluate_js.side_effect=evaluate;clock=[0]
        with tempfile.TemporaryDirectory() as d,patch('archive_desktop.require_cloud_ready'),patch('archive_desktop.pace_chat_read'),patch('archive_desktop.identity',return_value='account'),patch.object(BrowserWriter,'evaluate',return_value={'accessToken':'fixture'}),patch('archive_desktop.time.sleep',side_effect=lambda _:clock.__setitem__(0,clock[0]+100)),patch('archive_desktop.time.monotonic',side_effect=lambda:clock[0]):
            writer=BrowserWriter(window,controller,cloud,Path(d))
            if draft or voice_started:
                with self.assertRaises(NotSubmitted):writer('project','archive',text)
            elif confirmed:self.assertEqual(writer('project','archive',text),'archive')
            else:
                with self.assertRaises(TimeoutError):writer('project','archive',text)
        window.load_url.assert_called_with('about:blank')
        return state

    def test_cloud_confirmed_own_payload_is_cleared_from_editor(self):
        self.assertTrue(self.exercise()['cleared'])

    def test_unrecognized_user_draft_is_preserved_and_never_sent(self):
        state=self.exercise(draft='Unsent user notes')
        self.assertFalse(state['sent']);self.assertFalse(state['cleared']);self.assertEqual(state['draft'],'Unsent user notes')

    def test_missing_readback_never_clears_composer(self):
        self.assertFalse(self.exercise(confirmed=False)['cleared'])

    def test_voice_starting_after_navigation_blocks_submission(self):
        state=self.exercise(voice_started=True)
        self.assertFalse(state['sent']);self.assertFalse(state['cleared'])
