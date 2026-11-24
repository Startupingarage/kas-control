import json,os,tempfile,unittest,zipfile
from unittest.mock import patch
from diagnostics import save

class DiagnosticsTests(unittest.TestCase):
    def test_zip_uses_whitelist_and_never_copies_secrets_or_text(self):
        snapshot={'state':{'pid':2230,'phase':'idle','agent_connected':True,'error':'SECRET',
            'audio':{'sent':15,'face':{'text':'PRIVATE'},'neteq':{'packets':2,'secret':'SECRET'}}},
            'settings':{'voice':'orbit','chats':[{'conversation_id':'PRIVATE'}],'access_token':'SECRET'},
            'health':{'ready':True,'secret':'SECRET'}}
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'LOCALAPPDATA':d}):
            result=save(snapshot,{'running':False,'ordinary':{'text':'PRIVATE'}})
            with zipfile.ZipFile(result['path']) as package:
                raw=package.read('report.json').decode();report=json.loads(raw)
            self.assertNotIn('SECRET',raw);self.assertNotIn('PRIVATE',raw)
            self.assertEqual(report['audio'],{'sent':15});self.assertEqual(report['neteq'],{'packets':2})
            self.assertEqual(report['voice'],'orbit')
