"""Native Windows tray and single-instance ownership, using the existing WinForms host."""
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import threading
import winreg

RUN_KEY=r'Software\Microsoft\Windows\CurrentVersion\Run'

def install_autostart(executable):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,RUN_KEY) as key:
        winreg.SetValueEx(key,'KasControl',0,winreg.REG_SZ,'"'+str(Path(executable).resolve())+'" --tray')


class SingleInstance:
    def __init__(self,suffix=''):
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateMutexW.argtypes=[wintypes.LPVOID,wintypes.BOOL,wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype=wintypes.HANDLE
        self.kernel.CreateEventW.argtypes=[wintypes.LPVOID,wintypes.BOOL,wintypes.BOOL,wintypes.LPCWSTR]
        self.kernel.CreateEventW.restype=wintypes.HANDLE
        self.kernel.SetEvent.argtypes=[wintypes.HANDLE]
        self.kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        self.kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        name='Local\\KasControl-'+hashlib.sha256(os.environ['USERPROFILE'].encode()).hexdigest()[:16]+suffix
        self.mutex=self.kernel.CreateMutexW(None,False,name)
        self.existing=ctypes.get_last_error()==183
        self.event=self.kernel.CreateEventW(None,False,False,name+'-Show')
        if not self.mutex or not self.event:raise ctypes.WinError(ctypes.get_last_error())
        self.stopped=threading.Event();self.thread=None

    def notify(self):self.kernel.SetEvent(self.event)

    def listen(self,show):
        def run():
            while self.kernel.WaitForSingleObject(self.event,0xffffffff)==0:
                if self.stopped.is_set():return
                show()
        self.thread=threading.Thread(target=run,daemon=True,name='KasShowWindow');self.thread.start()

    def close(self):
        self.stopped.set();self.notify()
        if self.thread:self.thread.join(timeout=2)
        self.kernel.CloseHandle(self.event);self.kernel.CloseHandle(self.mutex)
