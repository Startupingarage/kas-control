import copy
import unittest
from backend import ControlError, make_settings, parse_projects, validate_id, Controller,Cloud

PROJECT = 'g-p-1234567890abcdef'
CID = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'

def settings():
    return {'version':1,'voice':'orbit','selected':'local-old','previous':'',
            'future_setting':{'keep':True},'chats':[{'id':'local-old','title':'Old',
             'conversation_id':CID,'gizmo_id':PROJECT,'parent_message_id':'old-parent',
             'voice_session_id':'old-session','account_id':'account','confirmed':True}]}

class BindingTests(unittest.TestCase):
    def test_new_keeps_voice_and_history(self):
        original=settings(); result,entry=make_settings(original,PROJECT,None,'New')
        self.assertEqual(original,settings())
        self.assertEqual(result['voice'],'orbit')
        self.assertEqual(result['future_setting'],original['future_setting'])
        self.assertEqual(result['chats'][0],original['chats'][0])
        self.assertEqual(result['previous'],'local-old')
        self.assertEqual(entry['gizmo_id'],PROJECT)
        self.assertEqual(entry['conversation_id'],'')
        self.assertNotIn('voice_session_id',entry)

    def test_existing_uses_latest_parent_not_voice_session(self):
        meta={'id':CID,'gizmo_id':PROJECT,'current_node':'latest-node','title':'Current'}
        result,entry=make_settings(settings(),PROJECT,meta,'')
        self.assertEqual(len(result['chats']),1)
        self.assertEqual(entry['parent_message_id'],'latest-node')
        self.assertNotIn('voice_session_id',entry)

    def test_wrong_project_rejected(self):
        with self.assertRaises(ControlError):
            make_settings(settings(),PROJECT,{'id':OTHER,'gizmo_id':'g-p-other','current_node':'n'},'')

    def test_no_project_and_archived_rejected(self):
        for meta in [{'id':CID,'current_node':'n'}, {'id':CID,'gizmo_id':PROJECT,'current_node':'n','is_archived':True}]:
            with self.assertRaises(ControlError): make_settings(settings(),PROJECT,meta,'')

    def test_ids_and_title_are_untrusted(self):
        for value in ['x;reboot','../settings','https://evil',"a'\n",'', 'x'*200]:
            with self.assertRaises(ControlError): validate_id(value)
        with self.assertRaises(ControlError): make_settings(settings(),PROJECT,None,'a\nb')

    def test_chat_limit_does_not_destroy_old_chats(self):
        s=settings();s['chats']*=64
        with self.assertRaises(ControlError): make_settings(s,PROJECT,None,'New')

    def test_nested_project_shape_and_archived(self):
        raw=[{'gizmo':{'gizmo':{'id':PROJECT,'display':{'name':'Core'},'is_archived':False}}},
             {'gizmo':{'gizmo':{'id':'g-p-hidden','display':{'name':'Hidden'},'is_archived':True}}}]
        self.assertEqual(parse_projects(raw),[{'id':PROJECT,'name':'Core'}])

class FakeStation:
    def __init__(self): self.doc=settings();self.actions=[];self.fail=False;self.phase='idle';self.error=''
    def read_settings(self): return copy.deepcopy(self.doc)
    def status(self): return {'phase':self.phase,'error':self.error,'pid':1,'agent_connected':True,'settings':self.doc}
    def command(self,action): self.actions.append(action);self.phase='active' if action=='open' else 'idle'
    def ensure_open(self,local_id,expected_hash):
        if self.doc['selected']!=local_id:raise ControlError('stale')
        if self.phase=='idle':self.command('open')
        if self.error or self.phase!='active':raise ControlError('open failed')
    def install(self,document,expected_hash,was_active):
        self.actions.append('install')
        if self.fail: raise ControlError('rolled back')
        self.doc=copy.deepcopy(document)
        entry=next(c for c in self.doc['chats'] if c['id']==self.doc['selected'])
        entry['conversation_id']=entry['conversation_id'] or OTHER
        return {'backup':'/data/opt/kas-control-backups/test','conversation_id':entry['conversation_id']}

class FakeCloud:
    def conversation(self,cid): return {'id':cid,'gizmo_id':PROJECT,'current_node':'latest','title':'Test'}
    def projects(self): return [{'id':PROJECT,'name':'Core'}]

class WorkflowTests(unittest.TestCase):
    def test_mismatch_does_not_close_session(self):
        station=FakeStation();cloud=FakeCloud();cloud.conversation=lambda _: {'id':CID,'gizmo_id':'g-p-other'}
        c=Controller(station,cloud)
        with self.assertRaises(ControlError):c.connect(PROJECT,CID,'')
        self.assertEqual(station.actions,[])

    def test_cloud_failure_does_not_close_session(self):
        station=FakeStation();cloud=FakeCloud()
        def fail(_):raise ControlError('offline')
        cloud.conversation=fail;c=Controller(station,cloud)
        with self.assertRaises(ControlError):c.connect(PROJECT,CID,'')
        self.assertEqual(station.actions,[])

    def test_create_preserves_voice(self):
        station=FakeStation();c=Controller(station,FakeCloud())
        result=c.connect(PROJECT,None,'New')
        self.assertEqual(station.doc['voice'],'orbit')
        self.assertEqual(result['conversation_id'],OTHER)

    def test_failed_install_does_not_retry_new_chat(self):
        station=FakeStation();station.fail=True;c=Controller(station,FakeCloud())
        with self.assertRaises(ControlError):c.connect(PROJECT,None,'New')
        self.assertEqual(station.actions.count('install'),1)
        self.assertEqual(station.doc,settings())

    def test_same_chat_does_not_replace_parent_or_restart(self):
        station=FakeStation();c=Controller(station,FakeCloud())
        result=c.connect(PROJECT,CID,'')
        self.assertTrue(result['unchanged'])
        self.assertEqual(station.actions,['open'])
        self.assertEqual(station.doc,settings())

    def test_same_chat_failed_open_is_not_success(self):
        station=FakeStation();c=Controller(station,FakeCloud())
        def fail(action):station.actions.append(action);station.error='voice_signaling_challenge_403'
        station.command=fail
        with self.assertRaises(ControlError):c.connect(PROJECT,CID,'')
        self.assertEqual(station.doc,settings())

class PaginationTests(unittest.TestCase):
    def test_all_pages_collected(self):
        cloud=Cloud(None);calls=[]
        def request(path,params):
            calls.append(params)
            return {'items':[2], 'cursor':None} if params.get('cursor') else {'items':[1],'cursor':'next'}
        cloud.request=request
        self.assertEqual(cloud.pages('/test',{'limit':1}),[1,2])
        self.assertEqual(calls[1]['cursor'],'next')
    def test_repeated_cursor_fails(self):
        cloud=Cloud(None);cloud.request=lambda *_:{'items':[],'cursor':'same'}
        with self.assertRaises(ControlError):cloud.pages('/test',{})

if __name__=='__main__':unittest.main()

class SnapshotTests(unittest.TestCase):
    def test_install_uses_locked_snapshot_not_later_selection(self):
        import json,hashlib
        from backend import Station
        doc=settings()
        state={'phase':'active','audio':{'conversation_mode':{'gizmo_id':PROJECT}}}
        raw=json.dumps(doc)
        payload=('KAS_STATE_BEGIN\n'+json.dumps(state)+'\nKAS_STATE_END\nKAS_SETTINGS_BEGIN\n'+raw+
                 '\nKAS_SETTINGS_END\nKAS_HASH:'+hashlib.sha256(raw.encode()).hexdigest()+'\n').encode()
        station=Station();station.run=lambda *a,**k:payload
        station.status=lambda: self.fail('must not read later state')
        station.read_settings=lambda: self.fail('must not read later settings')
        self.assertEqual(station.install(doc,'a'*64,True)['conversation_id'],CID)

    def test_same_chat_changed_before_open_is_not_success(self):
        station=FakeStation();controller=Controller(station,FakeCloud())
        def changed(local_id,expected_hash):
            station.doc['selected']='router-selected'
            FakeStation.ensure_open(station,local_id,expected_hash)
        station.ensure_open=changed
        with self.assertRaises(ControlError):controller.connect(PROJECT,CID)
        self.assertEqual(station.actions,[])
