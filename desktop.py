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
from archive_control import ArchiveControl

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
        self._archive=ArchiveControl()
        self._archive_hourly=None
        self._archive_timer=None
        self._close_requested=threading.Event()
        self._catalog_lock=threading.Lock()
        self._ui_closed=threading.Event()

    def _can_close(self):return not self._lock.locked()

    def _close_ui(self):
        if self._can_close():return True
        if not self._close_requested.is_set():
            self._close_requested.set()
            def finish():
                while not self._window.events.closed.is_set():
                    with self._lock:pass
                    self._window.destroy()
                    if self._window.events.closed.wait(.1):return
            threading.Thread(target=finish,daemon=True).start()
        return False

    def _archive_info(self,info):
        info=dict(info) if isinstance(info,dict) else {}
        from archive_schedule import enabled
        self._archive_hourly=enabled()
        info['hourly']=self._archive_hourly
        from archive_schedule import timer_error
        info['automatic_error']=self._archive_timer.last_error if self._archive_timer else timer_error()
        from archive_schedule import timer_status
        info['next_run_at']=timer_status().get('next_run_at')
        info['running']=self._archive.working
        info['available']=bool(info.get('available') and info['running'] and (info.get('updated') or 0)>=self._archive.started_at)
        return info

    def archive_status(self):
        # This reads only a local atomic status file and never starts SSH or WSL.
        if not self._window or not same_page(self._window.get_current_url(),self._window._server.address):
            return {'ok':False,'error':'Доступ разрешен только из окна пульта.'}
        return {'ok':True,'data':self._archive_info(self._manager.archive_status())}

    def _invoke(self,method,*args):
        lock=self._catalog_lock if method=='cloud_projects' else self._lock
        if not lock.acquire(blocking=False):return {'ok':False,'error':'Дождитесь завершения текущей операции.'}
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
            elif method=='start_archive':self._archive.start();data={}
            elif method=='stop_archive':self._archive.request_stop();data={}
            elif method=='set_archive_mode':
                from archive_schedule import set_enabled
                self._archive_hourly=set_enabled(args[0])
                if self._archive_timer:self._archive_timer.changed.set()
                data={'hourly':self._archive_hourly}
            elif method=='cloud_projects':
                try:data=self._manager.cloud_projects()
                except ControlError as error:
                    if not self._auto_login or not str(error).startswith('ChatGPT отклонил доступ') or self._ui_closed.is_set():raise
                    from browser_login import BrowserSession
                    self._browser=self._browser or BrowserSession(self._manager.station)
                    if self._ui_closed.is_set():self._browser.shutdown();raise ControlError('Окно пульта закрыто.')
                    self._browser.login(interactive=False)
                    if self._ui_closed.is_set():raise ControlError('Окно пульта закрыто.')
                    self._manager.cloud.http=self._browser
                    data=self._manager.cloud_projects()
            else:data=getattr(self._manager,method)(*args)
            if method in ('bootstrap','bootstrap_local') and isinstance(data,dict) and data.get('project_status'):
                data['project_status']['archive']=self._archive_info(data['project_status']['archive'])
            self._completed[method]=self._completed.get(method,0)+1
            return {'ok':True,'data':data}
        except ControlError as error:return {'ok':False,'error':str(error)}
        except Exception as error:
            import traceback
            self._last_error={'type':type(error).__name__,'frames':[(Path(f.filename).name,f.name,f.lineno) for f in traceback.extract_tb(error.__traceback__)]}
            return {'ok':False,'error':'Операция не подтверждена. Обновите состояние перед повторной попыткой.'}
        finally:lock.release()

    def save_project_command(self,project,phrase):return self._invoke('save_project_command',project,phrase)
    def remove_project_command(self,project):return self._invoke('remove_project_command',project)
    def open_project(self,project):return self._invoke('open_project',project)
    def reconnect_project(self,project):return self._invoke('reconnect_project',project)
    def diagnostics(self):return self._invoke('diagnostics')
    def open_diagnostics_folder(self):return self._invoke('open_diagnostics_folder')

    def bootstrap(self):return self._invoke('bootstrap')
    def bootstrap_local(self):return self._invoke('bootstrap_local')
    def cloud_projects(self):return self._invoke('cloud_projects')
    def set_voice(self,voice):return self._invoke('set_voice',voice)
    def start_archive(self):return self._invoke('start_archive')
    def stop_archive(self):return self._invoke('stop_archive')
    def set_archive_mode(self,mode):return self._invoke('set_archive_mode',mode)
    def set_ordinary_archive(self,project,mode):return self._invoke('set_ordinary_archive',project,mode)
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
    parser.add_argument('--tray',action='store_true');parser.add_argument('--tray-test')
    parser.add_argument('--ui-child',action='store_true');parser.add_argument('--ui-test',action='store_true')
    args=parser.parse_args()
    if args.check:
        try:
            manager=Manager();data=manager.bootstrap();chats=manager.list_chats(data['current']['project_id'])
            result={'ok':True,'projects':len(data['projects']),'chats':len(chats),'router_ready':data['router_ready'],
                    'voice':data['current']['voice'],'phase':data['current']['phase']}
        except Exception:result={'ok':False,'error':'Read-only connection check failed'}
        Path(args.check).write_text(json.dumps(result,indent=2),encoding='utf-8');return
    if not (args.ui_child or args.demo or args.preview or args.smoke):
        from tray_host import TrayHost
        TrayHost(test=bool(args.tray_test)).run(show=not bool(args.tray or args.tray_test),smoke=args.tray_test)
        return
    import webview
    webview.settings['ALLOW_DOWNLOADS']=False
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER']=False
    root=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))/'frontend'/'dist'
    demo=args.demo or bool(args.preview)
    api=None if demo else Bridge()
    if api and args.ui_child:
        from tray_host import TrayArchiveClient
        api._archive=TrayArchiveClient()
    title='Кас — пульт (проверка)' if args.ui_test else 'Кас — пульт'
    window=webview.create_window(title,StaticUI(root,demo),js_api=api,width=1240,height=820,
                                min_size=(980,640),background_color='#080d0b',text_select=True,
                                focus=not bool(args.preview or args.smoke),
                                hidden=bool(args.smoke),confirm_close=False)
    if api:
        api._window=window;api._auto_login=True;window.events.closing+=(api._close_ui if args.ui_child else api._can_close)
        def shutdown():
            api._ui_closed.set()
            if not args.ui_child:api._archive.stop()
            if api._browser:api._browser.shutdown()
        window.events.closed+=shutdown

    def before_show():
        from System.Drawing import Icon
        window.native.Icon=Icon(str(root.parent.parent/'assets'/'kas-control.ico'))
        # Block navigations before another document could receive the privileged bridge.
        def navigate(sender,event):
            if not same_page(str(event.Uri),window._server.address):event.Cancel=True
        window.native.webview.NavigationStarting+=navigate
        if args.ui_child:
            from System import Action
            from System.Windows.Forms import CloseReason,FormWindowState
            def minimize(sender,event):
                if window.native.WindowState==FormWindowState.Minimized:
                    window.native.BeginInvoke(Action(window.destroy))
            def system_close(sender,event):
                if event.CloseReason==CloseReason.WindowsShutDown:event.Cancel=False
            window.native.Resize+=minimize
            window.native.FormClosing+=system_close
    window.events.before_show+=before_show

    def inspect_window():
        if not (args.preview or args.smoke):return
        try:
            if not window.events.loaded.wait(30):raise RuntimeError('WebView did not load')
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                text=window.evaluate_js('document.body.innerText') or ''
                if (('Новый чат' in text and 'Подключить' in text or 'Новый разговор' in text) and
                    (not args.smoke or api._completed.get('bootstrap',0)>0 or api._completed.get('bootstrap_local',0)>0)):break
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
                diagnostic={'completed_calls':api._completed,'last_error':getattr(api,'_last_error',None),'url':window.get_current_url(),
                            'bridge':window.evaluate_js('({present:!!window.pywebview,methods:Object.keys(window.pywebview?.api||{}),text:document.body.innerText})')}
                Path(args.smoke).with_suffix('.diagnostic.json').write_text(json.dumps(diagnostic,ensure_ascii=False,indent=2),encoding='utf-8')
        finally:window.destroy()
    profile=str(Path(os.environ.get('LOCALAPPDATA',Path.home()))/'KasControl'/'WebView2')
    if args.ui_test:profile=str(Path(os.environ['LOCALAPPDATA'])/'KasControl'/'RuntimeTestWebView2')
    webview.start(inspect_window,gui='edgechromium',debug=False,private_mode=False,storage_path=profile)

if __name__=='__main__':main()
