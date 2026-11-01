import base64,json,time,unittest
from unittest.mock import Mock
from browser_login import identity,allowed_path,BrowserSession
from backend import ControlError
class BrowserLoginTests(unittest.TestCase):
 def test_same_account_with_different_login_providers(self):
  def token(sub,account='account',user='user'):
   c={'sub':sub,'exp':time.time()+60,'https://api.openai.com/auth':{'chatgpt_account_id':account,'chatgpt_user_id':user}}
   return 'x.'+base64.urlsafe_b64encode(json.dumps(c).encode()).decode().rstrip('=')+'.x'
  self.assertEqual(identity(token('auth0|old')),identity(token('google-oauth2|new')))
  self.assertNotEqual(identity(token('auth0|old')),identity(token('auth0|old',account='other')))
  self.assertNotEqual(identity(token('auth0|old')),identity(token('auth0|old',user='other')))
 def test_closing_login_window_hides_without_destroying_session(self):
  s=BrowserSession(Mock());s.window=Mock();s.authorized=True
  self.assertFalse(s._on_closing());s.window.hide.assert_called_once();s.window.destroy.assert_not_called()
  self.assertFalse(s.closed.is_set());self.assertTrue(s.authorized)
  s.shutdown();s.window.destroy.assert_called_once();self.assertTrue(s._on_closing())
 def test_identity_requires_subject_and_unexpired_claim(self):
  def token(claims):return 'x.'+base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')+'.x'
  self.assertEqual(identity(token({'sub':'user','exp':time.time()+60})),'user')
  for value in ('invalid',token({'sub':'user','exp':0}),token({'exp':time.time()+60})):
   with self.assertRaises(ControlError):identity(value)
 def test_transport_is_limited_to_management_endpoints(self):
  for p in ['/backend-api/gizmos/snorlax/sidebar?limit=20','/backend-api/gizmos/g-p-a/conversations','/backend-api/conversation/abc']:
   self.assertTrue(allowed_path(p,'GET'))
  self.assertTrue(allowed_path('/backend-api/projects','POST'))
  for p in ['https://evil.test/backend-api/projects','//evil.test/backend-api/projects','/backend-api/f/conversation','/backend-api/projects\nX:a','/backend-api/gizmos/a/../b']:
   self.assertFalse(allowed_path(p,'GET'));self.assertFalse(allowed_path(p,'POST'))
 def test_remote_bridge_transport_refuses_untrusted_page_and_paths(self):
  session=BrowserSession(Mock());session.authorized=True;session.window=Mock()
  session.window.get_current_url.return_value='https://evil.test/'
  session._evaluate=Mock()
  with self.assertRaises(ControlError):session.get('https://chatgpt.com/backend-api/gizmos/snorlax/sidebar')
  session.window.get_current_url.return_value='https://chatgpt.com/'
  for url in ['https://evil.test/backend-api/projects','https://chatgpt.com/backend-api/f/conversation']:
   with self.assertRaises(ControlError):session.post(url,json={})
  session._evaluate.assert_not_called()
 def test_post_is_not_retried_and_secrets_stay_out_of_result(self):
  session=BrowserSession(Mock());session.authorized=True;session.window=Mock()
  session.window.get_current_url.return_value='https://chatgpt.com/'
  session._evaluate=Mock(return_value={'status':200,'data':{'resource':{'id':'g-p-test'}}})
  result=session.post('https://chatgpt.com/backend-api/projects',json={'name':'Test'})
  self.assertEqual(result.json(),{'resource':{'id':'g-p-test'}});session._evaluate.assert_called_once()
if __name__=='__main__':unittest.main()
