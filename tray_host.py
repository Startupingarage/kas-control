"""Small Win32 tray owner; WebView2 exists only in the disposable UI child."""
import ctypes
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from archive_control import ArchiveControl
from archive_schedule import AppArchiveTimer
from backend import ControlError
from tray_app import SingleInstance,install_autostart

OPEN=0x8001
START=0x8002
STOP=0x8003
EXIT=0x8004
TRAY=0x8005
LRESULT=ctypes.c_ssize_t
PROC=ctypes.WINFUNCTYPE(LRESULT,W.HWND,W.UINT,W.WPARAM,W.LPARAM)

class WindowClass(ctypes.Structure):
    _fields_=[('style',W.UINT),('proc',PROC),('class_extra',ctypes.c_int),('window_extra',ctypes.c_int),
              ('instance',W.HINSTANCE),('icon',W.HICON),('cursor',W.HANDLE),('background',W.HBRUSH),
              ('menu',W.LPCWSTR),('name',W.LPCWSTR)]

class NotifyData(ctypes.Structure):
    _fields_=[('size',W.DWORD),('window',W.HWND),('id',W.UINT),('flags',W.UINT),('message',W.UINT),
              ('icon',W.HICON),('tip',W.WCHAR*128),('state',W.DWORD),('state_mask',W.DWORD),
              ('info',W.WCHAR*256),('timeout',W.UINT),('title',W.WCHAR*64),('info_flags',W.DWORD),
              ('guid',ctypes.c_byte*16),('balloon_icon',W.HICON)]

def api():
    user=ctypes.WinDLL('user32',use_last_error=True)
    signatures={
      'FindWindowW':(W.HWND,[W.LPCWSTR,W.LPCWSTR]),
      'CreateWindowExW':(W.HWND,[W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,W.HWND,W.HMENU,W.HINSTANCE,W.LPVOID]),
      'DefWindowProcW':(LRESULT,[W.HWND,W.UINT,W.WPARAM,W.LPARAM]),
      'PostMessageW':(W.BOOL,[W.HWND,W.UINT,W.WPARAM,W.LPARAM]),
      'SendMessageTimeoutW':(LRESULT,[W.HWND,W.UINT,W.WPARAM,W.LPARAM,W.UINT,W.UINT,ctypes.POINTER(ctypes.c_size_t)]),
      'LoadImageW':(W.HANDLE,[W.HINSTANCE,W.LPCWSTR,W.UINT,ctypes.c_int,ctypes.c_int,W.UINT]),
      'CreatePopupMenu':(W.HMENU,[]),
      'AppendMenuW':(W.BOOL,[W.HMENU,W.UINT,ctypes.c_size_t,W.LPCWSTR]),
      'TrackPopupMenu':(W.BOOL,[W.HMENU,W.UINT,ctypes.c_int,ctypes.c_int,ctypes.c_int,W.HWND,ctypes.POINTER(W.RECT)]),
      'DestroyMenu':(W.BOOL,[W.HMENU]),'DestroyWindow':(W.BOOL,[W.HWND]),
      'SetForegroundWindow':(W.BOOL,[W.HWND]),'ShowWindow':(W.BOOL,[W.HWND,ctypes.c_int]),
      'DestroyIcon':(W.BOOL,[W.HICON]),'RegisterClassW':(W.ATOM,[ctypes.POINTER(WindowClass)]),
      'GetMessageW':(W.BOOL,[ctypes.POINTER(W.MSG),W.HWND,W.UINT,W.UINT]),
      'DispatchMessageW':(LRESULT,[ctypes.POINTER(W.MSG)])}
    for name,(result,args) in signatures.items():
        function=getattr(user,name);function.restype=result;function.argtypes=args
    return user

def class_name():
    return 'KasControlTray-'+hashlib.sha256(os.environ['USERPROFILE'].encode()).hexdigest()[:16]

class TrayArchiveClient(ArchiveControl):
    def _send(self,message):
        user=api();window=user.FindWindowW(class_name(),None);result=ctypes.c_size_t()
        if not window or not user.SendMessageTimeoutW(window,message,0,0,2,20000,ctypes.byref(result)) or result.value!=1:
            raise ControlError('Нет ответа от приложения в трее. Перезапустите Kas Control.')
    def start(self):self._send(START)
    def stop(self):self._send(STOP)
    def request_stop(self):self._send(STOP)

class TrayHost:
    def __init__(self,test=False):
        self.user=api();self.archive=ArchiveControl();self.timer=AppArchiveTimer(self.archive)
        self.ui=None;self.ui_lock=threading.Lock();self.test=test;self.exiting=False
        self.ui_title='Кас — пульт (проверка)' if test else 'Кас — пульт'
        self.ready=threading.Event();self.error=''

    def open_ui(self):
        with self.ui_lock:
            if self.exiting:return
            if self.ui is not None and self.ui.poll() is None:
                window=self.user.FindWindowW(None,self.ui_title)
                if window:self.user.ShowWindow(window,9);self.user.SetForegroundWindow(window)
                return
            command=[sys.executable]
            if not getattr(sys,'frozen',False):command+=[str(Path(__file__).parent/'desktop.py')]
            command+=['--ui-child']
            if self.test:command+=['--demo','--ui-test']
            env=dict(os.environ,PYINSTALLER_RESET_ENVIRONMENT='1')
            self.ui=subprocess.Popen(command,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

    def exit(self):
        with self.ui_lock:
            if self.exiting:return
            self.exiting=True
        self.timer.stop();self.archive.stop()
        if self.ui is not None and self.ui.poll() is None:
            window=self.user.FindWindowW(None,self.ui_title)
            if window:self.user.PostMessageW(window,0x10,0,0)
            try:self.ui.wait(timeout=10)
            except subprocess.TimeoutExpired:
                subprocess.run(['taskkill.exe','/PID',str(self.ui.pid),'/T','/F'],capture_output=True,timeout=15,
                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.user.PostMessageW(self.window,0x10,0,0)

    def dispatch(self,window,message,wparam,lparam):
        try:
            if message==OPEN:self.open_ui();return 1
            if message==START:
                with self.ui_lock:
                    if self.exiting:return 0
                    self.archive.start()
                return 1
            if message==STOP:self.archive.request_stop();return 1
            if message==EXIT:
                threading.Thread(target=self.exit,daemon=True).start();return 1
            if message==TRAY:
                if lparam in (0x202,0x203):self.open_ui()
                elif lparam==0x205:
                    point=W.POINT();self.user.GetCursorPos(ctypes.byref(point));self.user.SetForegroundWindow(window)
                    choice=self.user.TrackPopupMenu(self.menu,0x100|0x2,point.x,point.y,0,window,None)
                    if choice:self.user.PostMessageW(window,{1:OPEN,2:START,3:EXIT}[choice],0,0)
                return 0
            if message==0x11:return 1
            if message==0x16 and wparam:
                self.timer.stop();self.archive.stop();self.user.DestroyWindow(window);return 0
            if message==0x10:self.user.DestroyWindow(window);return 0
            if message==0x2:self.user.PostQuitMessage(0);return 0
        except Exception as error:
            self.error=str(error)[:200]
            self.archive.root.mkdir(parents=True,exist_ok=True)
            (self.archive.root/'tray-error.log').write_text(self.error,encoding='utf-8')
            return 0
        return self.user.DefWindowProcW(window,message,wparam,lparam)

    def run(self,show=False,smoke=None):
        self.instance=SingleInstance('-LightTest' if self.test else '')
        if self.instance.existing:
            self.instance.notify();self.instance.close();return
        if getattr(sys,'frozen',False) and not self.test:install_autostart(sys.executable)
        kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.GetModuleHandleW.restype=W.HMODULE
        module=kernel.GetModuleHandleW(None)
        self.callback=PROC(self.dispatch)
        name=class_name()+('-Test' if self.test else '')
        cls=WindowClass();cls.proc=self.callback;cls.instance=module;cls.name=name
        if not self.user.RegisterClassW(ctypes.byref(cls)):raise ctypes.WinError(ctypes.get_last_error())
        self.window=self.user.CreateWindowExW(0,name,'Kas Control tray',0,0,0,0,0,None,None,module,None)
        if not self.window:raise ctypes.WinError(ctypes.get_last_error())
        root=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))
        icon=self.user.LoadImageW(None,str(root/'assets/kas-control.ico'),1,32,32,0x10)
        self.data=NotifyData();self.data.size=ctypes.sizeof(NotifyData);self.data.window=self.window
        self.data.id=1;self.data.flags=7;self.data.message=TRAY;self.data.icon=icon;self.data.tip='Kas Control'
        shell=ctypes.WinDLL('shell32');shell.Shell_NotifyIconW.argtypes=[W.DWORD,ctypes.POINTER(NotifyData)]
        if not shell.Shell_NotifyIconW(0,ctypes.byref(self.data)):raise ctypes.WinError()
        self.menu=self.user.CreatePopupMenu()
        for number,title in [(1,'Открыть Kas Control'),(2,'Архивировать сейчас'),(3,'Выйти')]:
            self.user.AppendMenuW(self.menu,0,number,title)
        self.instance.listen(lambda:self.user.PostMessageW(self.window,OPEN,0,0))
        self.timer.start();self.ready.set()
        if show:self.open_ui()
        if smoke:threading.Thread(target=self.smoke,args=(smoke,),daemon=True).start()
        message=W.MSG()
        try:
            while self.user.GetMessageW(ctypes.byref(message),None,0,0)>0:
                self.user.TranslateMessage(ctypes.byref(message));self.user.DispatchMessageW(ctypes.byref(message))
        finally:
            self.timer.stop();self.archive.stop()
            shell.Shell_NotifyIconW(2,ctypes.byref(self.data));self.user.DestroyIcon(icon);self.user.DestroyMenu(self.menu)
            self.instance.close()

    def smoke(self,path):
        result={'initial_ui_absent':self.ui is None,'webview_imported_in_host':'webview' in sys.modules}
        try:
            previous=None
            for cycle in range(2):
                self.open_ui();child=self.ui
                deadline=time.monotonic()+45
                while time.monotonic()<deadline and not self.user.FindWindowW(None,self.ui_title):time.sleep(.1)
                window=self.user.FindWindowW(None,self.ui_title)
                if not window:raise RuntimeError('UI did not open')
                if previous is not None:result['reopen_new_process']=child.pid!=previous
                self.user.PostMessageW(window,0x10,0,0);child.wait(timeout=30)
                result['cycle_'+str(cycle)+'_ui_exited']=child.returncode==0;previous=child.pid
            result['host_still_alive']=True
        except Exception as error:result['error']=str(error)
        finally:
            Path(path).write_text(json.dumps(result),encoding='utf-8');self.user.PostMessageW(self.window,EXIT,0,0)
