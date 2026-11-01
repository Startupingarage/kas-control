"""ChatGPT web login and same-origin browser transport, without a native remote bridge."""
import base64,json,threading,time
from urllib.parse import urlsplit,urlencode
from backend import B,ControlError

def identity(token,allow_expired=False):
    try:
        claims=json.loads(base64.urlsafe_b64decode(token.split('.')[1]+'==='))
        if (not allow_expired and claims.get('exp',0)<=time.time()) or not claims.get('sub'):raise ValueError()
        auth=claims.get('https://api.openai.com/auth') or {}
        account=auth.get('chatgpt_account_id');user=auth.get('chatgpt_user_id') or auth.get('user_id')
        if account and user:return account,user
        return claims['sub']
    except Exception:raise ControlError('Не удалось проверить учётную запись ChatGPT.') from None

def allowed_path(path,method):
    if not isinstance(path,str) or len(path)>4096 or any(c in path for c in ('\\','\r','\n','#')):return False
    parts=urlsplit(path)
    if parts.scheme or parts.netloc or not parts.path.startswith('/backend-api/'):return False
    import re
    p=parts.path.removeprefix('/backend-api')
    if method=='GET':return bool(re.fullmatch(r'/gizmos/(snorlax/sidebar|[A-Za-z0-9_-]+(?:/conversations)?)|/conversation/[A-Za-z0-9_-]+',p))
    return method=='POST' and p=='/projects'

class BrowserResponse:
    def __init__(self,status,data):self.status_code=status;self._data=data
    def json(self):return self._data

class BrowserSession:
    def __init__(self,station):
        self.station=station;self.window=None;self.closed=threading.Event();self.authorized=False
        self.cancelled=threading.Event();self.shutting_down=False
    def _on_closing(self):
        if self.shutting_down:return True
        self.window.hide()
        if not self.authorized:self.cancelled.set()
        return False
    def shutdown(self):
        self.shutting_down=True
        if self.window and not self.closed.is_set():self.window.destroy()
    def _evaluate(self,script,timeout=30):
        done=threading.Event();result=[]
        self.window.evaluate_js(script,lambda value:(result.append(value),done.set()))
        if not done.wait(timeout):raise ControlError('ChatGPT не ответил в веб-окне. Проверьте подключение.')
        return result[0]
    def _chatgpt_page(self):
        p=urlsplit(self.window.get_current_url() or '')
        return p.scheme=='https' and p.hostname=='chatgpt.com' and not p.username and not p.password
    def login(self,interactive=True):
        import webview
        auth=json.loads(self.station.run('cat '+B+'/state/web-session.json'))
        expected=identity(auth.get('access_token',''),allow_expired=True)
        self.authorized=False
        self.cancelled.clear()
        if self.window is None or self.closed.is_set():
            self.closed.clear()
            self.window=webview.create_window('Вход в ChatGPT — Кас', 'https://chatgpt.com/',width=1040,height=780,js_api=None,hidden=not interactive)
            self.window.events.closed+=self.closed.set
            self.window.events.closing+=self._on_closing
        elif interactive:self.window.show()
        deadline=time.monotonic()+(900 if interactive else 30)
        while time.monotonic()<deadline and not self.closed.is_set() and not self.cancelled.is_set():
            if self._chatgpt_page():
                try:
                    value=self._evaluate("fetch('/api/auth/session',{credentials:'include',redirect:'error'}).then(async r=>({status:r.status,data:r.ok?await r.json():null})).catch(()=>({status:0}))",timeout=15)
                    if isinstance(value,dict) and value.get('status')==200 and (value.get('data') or {}).get('accessToken'):
                        actual=identity(value['data']['accessToken'])
                        if actual!=expected:raise ControlError('Войдите в тот же аккаунт ChatGPT, который подключён к колонке.')
                        self.authorized=True;self.window.hide();return {'signed_in':True}
                    if not interactive and isinstance(value,dict) and (value.get('status') in (200,401,403)):
                        raise ControlError('Нажмите «Войти в ChatGPT» в пульте.')
                except ControlError:raise
                except Exception:pass
            self.cancelled.wait(2)
        raise ControlError('Вход не завершён. Откройте окно входа ещё раз.')
    def _request(self,method,url,params=None,json_body=None):
        if not self.authorized or self.closed.is_set():raise ControlError('Нажмите «Войти в ChatGPT» в пульте.')
        prefix='https://chatgpt.com'
        if not url.startswith(prefix+'/'):raise ControlError('Недопустимый адрес ChatGPT.')
        path=url[len(prefix):]
        if params:path+=('&' if '?' in path else '?')+urlencode(params)
        if not allowed_path(path,method) or not self._chatgpt_page():raise ControlError('Недопустимый запрос из окна входа.')
        options={'method':method,'credentials':'include','redirect':'error','headers':{'Content-Type':'application/json'}}
        if json_body is not None:options['body']=json.dumps(json_body,ensure_ascii=False)
        # Authorization stays inside the browser; only endpoint JSON returns to Python.
        script="(async()=>{const s=await fetch('/api/auth/session',{credentials:'include',redirect:'error'}).then(r=>r.json());if(!s.accessToken)return {status:401};const o="+json.dumps(options)+";o.headers.Authorization='Bearer '+s.accessToken;const r=await fetch("+json.dumps(path)+",o);const t=await r.text();if(t.length>8388608)return {status:413};let data=null;try{data=JSON.parse(t)}catch{}return {status:r.status,data};})()"
        value=self._evaluate(script)
        if not isinstance(value,dict):raise ControlError('Не удалось прочитать ответ веб-клиента ChatGPT.')
        return BrowserResponse(value.get('status',0),value.get('data'))
    def get(self,url,params=None,**kwargs):return self._request('GET',url,params=params)
    def post(self,url,json=None,**kwargs):return self._request('POST',url,json_body=json)
