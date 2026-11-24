"""Keep archive drafts separate; share only encrypted ChatGPT login cookies."""
import ctypes
import json
import os
from pathlib import Path
import time
import uuid
from ctypes import wintypes

class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]

def protect(data, decrypt=False):
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));target=Blob()
    function=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):
        raise ctypes.WinError()
    try:return ctypes.string_at(target.data,target.size)
    finally:ctypes.windll.kernel32.LocalFree(target.data)

def cookie_file():return Path(os.environ['LOCALAPPDATA'])/'KasControl/Archive/session.dpapi'

def ui(window, action):
    from System import Action
    deadline=time.monotonic()+20
    while not getattr(window.native,'webview',None):
        if time.monotonic()>deadline:raise TimeoutError('Archive browser handle timeout')
        time.sleep(.1)
    window.native.webview.Invoke(Action(action))

def export_archive_login(window):
    result={}
    ui(window,lambda:result.update(task=window.native.webview.CoreWebView2.CookieManager.GetCookiesAsync('https://chatgpt.com')))
    deadline=time.monotonic()+10
    while not result['task'].IsCompleted:
        if time.monotonic()>deadline:raise TimeoutError('Cookie export timeout')
        time.sleep(.1)
    def collect():
        result['cookies']=[dict(name=c.Name,value=c.Value,domain=c.Domain,path=c.Path,
                                http_only=c.IsHttpOnly,secure=c.IsSecure,same_site=int(c.SameSite),
                                expires=None if c.IsSession else c.Expires.ToUniversalTime().ToString('o'))
                           for c in result['task'].Result]
    ui(window,collect)
    if not result['cookies']:raise ValueError('No ChatGPT cookies')
    path=cookie_file();path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('session-'+uuid.uuid4().hex+'.new')
    temporary.write_bytes(protect(json.dumps(result['cookies']).encode()))
    os.replace(temporary,path)

def import_archive_login(window):
    path=cookie_file()
    importing=path.with_suffix('.importing')
    if path.exists():os.replace(path,importing)
    elif not importing.exists():return False
    ready={}
    for _ in range(200):
        ui(window,lambda:ready.update(core=window.native.webview.CoreWebView2 is not None))
        if ready['core']:break
        time.sleep(.1)
    else:raise TimeoutError('Archive browser initialization timeout')
    cookies=json.loads(protect(importing.read_bytes(),decrypt=True))
    if not isinstance(cookies,list) or len(cookies)>100:raise ValueError('Invalid cookie transfer')
    def install():
        from System import DateTime
        from Microsoft.Web.WebView2.Core import CoreWebView2CookieSameSiteKind
        manager=window.native.webview.CoreWebView2.CookieManager
        for item in cookies:
            if item['domain'].lstrip('.')!='chatgpt.com':raise ValueError('Invalid cookie domain')
            cookie=manager.CreateCookie(item['name'],item['value'],item['domain'],item['path'])
            cookie.IsHttpOnly=item['http_only'];cookie.IsSecure=item['secure']
            cookie.SameSite=CoreWebView2CookieSameSiteKind(item['same_site'])
            if item['expires']:cookie.Expires=DateTime.Parse(item['expires'])
            manager.AddOrUpdateCookie(cookie)
    ui(window,install)
    importing.unlink()
    return True
