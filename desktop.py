"""Portable WebView2 shell. The frontend has a narrow, serialized native API."""
import argparse
import json
import mimetypes
import os
from pathlib import Path
import sys
import threading
import time
from urllib.parse import unquote, urlsplit
from backend import ControlError
from management import Manager

def same_page(actual,expected):
    try:
        a,b=urlsplit(actual or ''),urlsplit(expected or '')
        return (a.scheme==b.scheme=='http' and a.hostname==b.hostname=='127.0.0.1'
                and a.port==b.port and a.path in ('/','/index.html')
                and not a.username and not a.password)
    except ValueError:return False

class StaticUI:
    def __init__(self,root,demo=False):self.root=Path(root).resolve();self.demo=demo
    def __call__(self,environ,start_response):
        method=environ.get('REQUEST_METHOD','GET')
        if method not in ('GET','HEAD'):
            start_response('405 Method Not Allowed',[('Content-Type','text/plain')]);return [b'Method not allowed']
        path=unquote(environ.get('PATH_INFO','/'))
        if self.demo and path in ('/','/index.html') and 'demo=1' not in environ.get('QUERY_STRING',''):
            start_response('302 Found',[('Location','/?demo=1')]);return [b'']
        try:
            file=(self.root/('index.html' if path=='/' else path.lstrip('/'))).resolve()
            if not file.is_relative_to(self.root) or not file.is_file():raise ValueError()
            data=file.read_bytes()
        except (OSError,ValueError):
            start_response('404 Not Found',[('Content-Type','text/plain')]);return [b'Not found']
        headers=[('Content-Type',mimetypes.guess_type(file.name)[0] or 'application/octet-stream'),
                 ('Content-Length',str(len(data))),('Cache-Control','no-store'),
                 ('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY')]
        start_response('200 OK',headers)
        return [b'' if method=='HEAD' else data]

class Bridge:
    def __init__(self,manager=None):
        self._manager=manager or Manager();self._lock=threading.Lock();self._window=None;self._completed={};self._browser=None;self._auto_login=False

    def _can_close(self):return not self._lock.locked()

    def _invoke(self,method,*args):
        if not self._lock.acquire(blocking=False):return {'ok':False,'error':'Дождитесь завершения текущей операции.'}
        try:
            if not self._window or not same_page(self._window.get_current_url(),self._window._server.address):
                raise ControlError('Доступ разрешён только из окна пульта.')
            if method=='bootstrap' and self._auto_login:
                from browser_login import BrowserSession
                self._browser=self._browser or BrowserSession(self._manager.station)
                if not self._browser.authorized or self._browser.closed.is_set():self._browser.login(interactive=False)
                self._manager.cloud.http=self._browser
            if method=='login_chatgpt':
                from browser_login import BrowserSession
                self._browser=self._browser or BrowserSession(self._manager.station)
                data=self._browser.login();self._manager.cloud.http=self._browser
            else:data=getattr(self._manager,method)(*args)
            self._completed[method]=self._completed.get(method,0)+1
            return {'ok':True,'data':data}
        except ControlError as error:return {'ok':False,'error':str(error)}
        except Exception:return {'ok':False,'error':'Операция не подтверждена. Обновите состояние перед повторной попыткой.'}
        finally:self._lock.release()

    def bootstrap(self):return self._invoke('bootstrap')
    def login_chatgpt(self):return self._invoke('login_chatgpt')
    def list_chats(self,project):return self._invoke('list_chats',project)
    def create_project(self,name):return self._invoke('create_project',name)
    def create_chat(self,project,title,phrase=''):return self._invoke('create_chat',project,title,phrase)
    def save_command(self,project,key,phrase):return self._invoke('save_command',project,key,phrase)
    def remove_command(self,project,key):return self._invoke('remove_command',project,key)
    def connect_chat(self,project,key):return self._invoke('connect_chat',project,key)
    def close_session(self):return self._invoke('close_session')
    def restore_binding(self):return self._invoke('restore_binding')
    def open_in_chatgpt(self,project,key):return self._invoke('open_in_chatgpt',project,key)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--demo',action='store_true')
    parser.add_argument('--preview');parser.add_argument('--check');parser.add_argument('--smoke')
    args=parser.parse_args()
    if args.check:
        try:
            manager=Manager();data=manager.bootstrap();chats=manager.list_chats(data['current']['project_id'])
            result={'ok':True,'projects':len(data['projects']),'chats':len(chats),'router_ready':data['router_ready'],
                    'voice':data['current']['voice'],'phase':data['current']['phase']}
        except Exception:result={'ok':False,'error':'Read-only connection check failed'}
        Path(args.check).write_text(json.dumps(result,indent=2),encoding='utf-8');return
    import webview
    webview.settings['ALLOW_DOWNLOADS']=False
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER']=False
    root=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))/'frontend'/'dist'
    demo=args.demo or bool(args.preview)
    api=None if demo else Bridge()
    window=webview.create_window('Кас — пульт',StaticUI(root,demo),js_api=api,width=1240,height=820,
                                min_size=(980,640),background_color='#080d0b',text_select=True,
                                focus=not bool(args.preview or args.smoke),
                                hidden=bool(args.smoke),confirm_close=False)
    if api:
        api._window=window;api._auto_login=True;window.events.closing+=api._can_close
        window.events.closed+=lambda:api._browser.shutdown() if api._browser else None

    def before_show():
        from System.Drawing import Icon
        window.native.Icon=Icon(str(root.parent.parent/'assets'/'kas-control.ico'))
        # Block navigations before another document could receive the privileged bridge.
        def navigate(sender,event):
            if not same_page(str(event.Uri),window._server.address):event.Cancel=True
        window.native.webview.NavigationStarting+=navigate
    window.events.before_show+=before_show

    def inspect_window():
        if not (args.preview or args.smoke):return
        try:
            if not window.events.loaded.wait(30):raise RuntimeError('WebView did not load')
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                text=window.evaluate_js('document.body.innerText') or ''
                if ('Новый чат' in text and 'Подключить' in text and
                    (not args.smoke or api._completed.get('list_chats',0)>0)):break
                time.sleep(.2)
            else:raise RuntimeError('Frontend did not become ready')
            if args.preview:
                from System import Action
                from System.IO import FileStream, FileMode, FileAccess
                from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
                done=threading.Event();fail=[]
                def capture():
                    stream=FileStream(str(Path(args.preview).resolve()),FileMode.Create,FileAccess.Write)
                    task=window.native.webview.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,stream)
                    def wait():
                        try:task.Wait()
                        except Exception:fail.append(True)
                        finally:stream.Close();done.set()
                    threading.Thread(target=wait,daemon=True).start()
                window.native.webview.Invoke(Action(capture))
                if not done.wait(20) or fail:raise RuntimeError('WebView capture failed')
            if args.smoke:
                Path(args.smoke).write_text(json.dumps({'rendered':True,'url_allowed':same_page(window.get_current_url(),window._server.address),'completed_calls':api._completed},ensure_ascii=False,indent=2),encoding='utf-8')
        except Exception as error:
            path=Path(args.preview or args.smoke).with_suffix('.error.txt')
            path.write_text(str(error),encoding='utf-8')
            if args.smoke:
                diagnostic={'completed_calls':api._completed,'url':window.get_current_url(),
                            'bridge':window.evaluate_js('({present:!!window.pywebview,methods:Object.keys(window.pywebview?.api||{}),text:document.body.innerText})')}
                Path(args.smoke).with_suffix('.diagnostic.json').write_text(json.dumps(diagnostic,ensure_ascii=False,indent=2),encoding='utf-8')
        finally:window.destroy()
    profile=str(Path(os.environ.get('LOCALAPPDATA',Path.home()))/'KasControl'/'WebView2')
    webview.start(inspect_window,gui='edgechromium',debug=False,private_mode=False,storage_path=profile)

if __name__=='__main__':main()
