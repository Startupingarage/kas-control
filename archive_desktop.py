"""Hidden Windows archive worker. No voice mutations, OS input or visible windows."""
import argparse
import json
import os
from pathlib import Path
import re
import threading
import time

from backend import B, Controller, ControlError, validate_id
from browser_login import identity
from archive_worker import ArchiveWorker, NotSubmitted, QueueMonitor, atomic_json, recoverable_archive_draft
from ordinary_archive import rate_limit_delay
from chat_requests import pace_chat_read,defer_chat_reads,record_rate_limit,require_cloud_ready

BASE='https://chatgpt.com/backend-api'
# Single-line JSON payloads can acquire visual line breaks around styled links.
EDITOR_TEXT="(e=>e?.children.length===1&&e.firstElementChild.tagName==='P'&&!e.querySelector('br,div,p p')?e.textContent:e?.innerText)"

def progress_status(root,report,ordinary=None):
    if ordinary is not None:
        report['ordinary']=dict(ordinary)
        report['processing_source']=ordinary.get('processing_source')
    waiting=report.get('voice_phase') not in (None,'idle') or report.get('ordinary',{}).get('deferred')=='voice_active'
    report.update(note='voice_active' if waiting else 'running',heartbeat_at=time.time())
    report.pop('completed_at',None)
    atomic_json(root/'status.json',report)

def final_note(root,report,monitor,status):
    ordinary=report.get('ordinary',{})
    if report.get('error'):return 'error'
    if rate_limit_delay(root,monitor):return 'rate_limited'
    if status.get('phase')!='idle' or ordinary.get('deferred')=='voice_active':return 'voice_active'
    if report.get('blocked_sources') or ordinary.get('errors'):return 'attention'
    if ordinary.get('deferred') or ordinary.get('pending') or report.get('remaining_sources'):return 'incomplete'
    return 'checked'

def deadline_status(root,report,monitor):
    report.setdefault('ordinary',{})['deferred']='batch_limit'
    report.update(completed_at=time.time(),note=final_note(root,report,monitor,{'phase':report.get('voice_phase','idle')}))
    report.pop('processing_source',None)
    atomic_json(root/'status.json',report)


class ArchiveCloud:
    def __init__(self,controller):self.controller=controller;self.chat_versions={};self.titles={}
    def require_idle(self):
        if self.controller.station.status().get('phase')!='idle':
            raise ControlError('Voice is active or its state is not confirmed')
    def request(self,method,path,**kwargs):
        self.require_idle()
        require_cloud_ready()
        cloud=self.controller.cloud
        if cloud.http is None:cloud.transport()
        if method=='get' and path.startswith('/conversation/'):pace_chat_read()
        require_cloud_ready()
        self.require_idle()  # A conversation may start while a rate-limit pause is running.
        response=getattr(cloud.http,method)(BASE+path,timeout=30,allow_redirects=False,**kwargs)
        if response.status_code==429:
            defer_chat_reads(response.headers.get('Retry-After'));record_rate_limit(response.headers.get('Retry-After'))
        if response.status_code in (401,403):cloud.http=None
        return response
    def read(self,cid):
        response=self.request('get','/conversation/'+validate_id(cid))
        if response.status_code==404:return None
        if response.status_code!=200:raise ControlError('Conversation read HTTP '+str(response.status_code))
        result=response.json()
        if result.get('conversation_id')!=cid:raise ControlError('Conversation identity mismatch')
        self.titles[cid]=result.get('title')
        return result
    def chats(self,project):
        self.require_idle()
        rows=self.controller.cloud.chats(project)
        self.chat_versions.update({(project,c['id']):c.get('updated') for c in rows})
        return [c['id'] for c in rows]
    def rename(self,cid,title):
        if self.titles.get(cid)==title:return
        current=self.read(cid)
        if current and current.get('title')==title:return
        response=self.request('post','/conversation/id/'+validate_id(cid)+'/rename',json={'title':title})
        if response.status_code!=200:raise ControlError('Archive rename HTTP '+str(response.status_code))
    def delete(self,cid):
        response=self.request('delete','/conversation/id/'+validate_id(cid),params={'delete_generated_artifacts':'false','delete_uploaded_artifacts':'false'})
        if response.status_code not in (200,204):raise ControlError('Deletion HTTP '+str(response.status_code)+'; reconcile before retry')


class BrowserWriter:
    def __init__(self,window,controller,cloud,root):
        self.window=window;self.controller=controller;self.cloud=cloud;self.root=root
    def evaluate(self,script):
        done=threading.Event();result=[]
        self.window.evaluate_js(script,lambda value:(result.append(value),done.set()))
        if not done.wait(25):raise TimeoutError('Web client request timed out')
        return result[0]
    def heartbeat(self):
        path=self.root/'status.json'
        if path.exists():
            report=json.loads(path.read_text(encoding='utf-8'));progress_status(self.root,report)
    def require_idle(self):
        if self.controller.station.status().get('phase')!='idle':
            raise NotSubmitted('Voice is active or its state is not confirmed')
        require_cloud_ready()
    def __call__(self,project,cid,payload):
        may_have_sent=False
        self.confirmed_archive=None
        try:
            self.heartbeat()
            self.require_idle()
            validate_id(project)
            if cid:validate_id(cid)
            if len(payload)>200000:raise NotSubmitted('Archive message too large; retain source for review')
            expected=identity(json.loads(self.controller.station.run('cat '+B+'/state/web-session.json'))['access_token'],allow_expired=True)
            path='/g/'+project+('/c/'+cid if cid else '/project')
            if cid:pace_chat_read()  # Navigation also reads this conversation.
            self.require_idle()
            self.window.load_url('https://chatgpt.com'+path)
            deadline=time.monotonic()+45
            while time.monotonic()<deadline:
                self.require_idle()
                self.heartbeat()
                state=self.window.evaluate_js("({host:location.hostname,path:location.pathname,count:document.querySelectorAll('[contenteditable=true][role=textbox]').length})")
                if (state and state.get('host')=='chatgpt.com' and state.get('count')==1
                        and ((cid and state.get('path','').endswith('/c/'+cid)) or (not cid and state.get('path','').split('/')[2].startswith(project) and state['path'].endswith('/project')))):
                    break
                time.sleep(1)
            else:raise NotSubmitted('ChatGPT web client unavailable; source retained')
            auth=self.evaluate("fetch('/api/auth/session',{credentials:'include'}).then(r=>r.json())")
            if identity(auth.get('accessToken',''))!=expected:raise NotSubmitted('Wrong ChatGPT account')
            del auth
            draft=self.window.evaluate_js(EDITOR_TEXT+"(document.querySelector('[contenteditable=true][role=textbox]')).trim()")
            if draft and draft!=payload:
                if not recoverable_archive_draft(draft,self.root,project):
                    raise NotSubmitted('Existing composer draft retained; no overwrite')
                import hashlib
                atomic_json(self.root/('recovered-draft-'+hashlib.sha256(draft.encode()).hexdigest()+'.json'),
                            {'project_id':project,'archive_id':cid,'text':draft})
            if draft!=payload:
                inserted=self.window.evaluate_js("(()=>{const e=document.querySelector('[contenteditable=true][role=textbox]');if("+EDITOR_TEXT+"(e).trim()!=="+json.dumps(draft)+")return false;e.focus();const r=document.createRange();r.selectNodeContents(e);const s=getSelection();s.removeAllRanges();s.addRange(r);return document.execCommand('insertText',false,"+json.dumps(payload)+");})()")
                if not inserted:raise NotSubmitted('Plain text insertion failed; source retained')
            time.sleep(1)
            selector='button[type="submit"][aria-label="Отправить"]'
            check=self.window.evaluate_js("({text:"+EDITOR_TEXT+"(document.querySelector('[contenteditable=true][role=textbox]')),buttons:Array.from(document.querySelectorAll("+json.dumps(selector)+")).filter(x=>!x.disabled).length})")
            if check.get('text')!=payload or check.get('buttons')!=1:raise NotSubmitted('Exact text or send control not confirmed')
            if cid:
                target=self.cloud.read(cid)
                if not target or target.get('gizmo_id')!=project or target.get('is_archived'):
                    raise NotSubmitted('Reserved chat moved before submission')
            # Unknown outcome from this point must be reconciled, never blindly resubmitted.
            self.require_idle()
            may_have_sent=True
            clicked=self.window.evaluate_js("(()=>{const e=document.querySelector('[contenteditable=true][role=textbox]');const b=document.querySelectorAll("+json.dumps(selector)+");if("+EDITOR_TEXT+"(e)!=="+json.dumps(payload)+"||b.length!==1||b[0].disabled)return false;b[0].click();return true;})()")
            if not clicked:raise NotSubmitted('Send precondition changed; no click')
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                self.heartbeat()
                current=self.window.evaluate_js('location.pathname')
                match=re.search(r'/c/([A-Za-z0-9_-]+)$',current or '')
                if match:
                    actual=match.group(1)
                    if cid and actual!=cid:raise RuntimeError('Unexpected archive navigation')
                    saved=self.cloud.read(actual)
                    if saved and saved.get('gizmo_id')==project and any(
                        (n.get('message') or {}).get('author',{}).get('role')=='user'
                        and (n.get('message') or {}).get('content',{}).get('parts')==[payload]
                        for n in saved.get('mapping',{}).values()):
                        # Clear only our exact, cloud-confirmed text if the web client retained it as a draft.
                        self.window.evaluate_js("(()=>{const e=document.querySelector('[contenteditable=true][role=textbox]');if("+EDITOR_TEXT+"(e)!=="+json.dumps(payload)+")return false;e.focus();const r=document.createRange();r.selectNodeContents(e);const s=getSelection();s.removeAllRanges();s.addRange(r);document.execCommand('delete');return true;})()")
                        time.sleep(1)
                        self.confirmed_archive=saved
                        return actual
                time.sleep(2)
            raise TimeoutError('Submission not confirmed; reconcile cloud history')
        except NotSubmitted:raise
        except Exception as error:
            if not may_have_sent:
                detail=str(error)[:150] if isinstance(error,ControlError) else type(error).__name__
                raise NotSubmitted(detail+': web client preparation failed') from None
            raise
        finally:
            # Release model streaming and DOM memory; the persistent login is retained.
            self.window.load_url('about:blank')


def read_closed_state(controller,root):
    if (root/'stop').exists():raise ControlError('Archive worker stopping')
    raw=controller.station.run('test ! -f /data/opt/kas-command-router/pending.json && cat /data/opt/kas-command-router/session-identities/*.json').decode()
    identities=[];decoder=json.JSONDecoder()
    while raw.strip():
        raw=raw.lstrip();entry,end=decoder.raw_decode(raw);identities.append(entry);raw=raw[end:]
    routes=json.loads(controller.station.run('cat /data/opt/kas-command-router/routes.json'))
    status=controller.station.status()
    return identities,status,routes


class BrowserRequired(NotSubmitted):needs_browser=True

def prepare_batch(root,controller,deadline=None):
    """Read-only discovery and existing-copy recovery do not need a browser."""
    cloud=ArchiveCloud(controller)
    def need_browser(*args):raise BrowserRequired('Browser required for archive submission')
    worker=ArchiveWorker(root,cloud,need_browser,lambda:read_closed_state(controller,root))
    monitor=QueueMonitor(root/'observations.json')
    identities,status,routes=read_closed_state(controller,root)
    projects={r['project_id'] for r in routes.get('routes',[])}
    candidates=[i for i in identities if i.get('project_session') is True and i.get('conversation_id') and i.get('gizmo_id') in projects and worker.job(i['conversation_id']).get('state')!='deleted']
    candidates.sort(key=lambda i:monitor.priority(i['conversation_id'],worker.job(i['conversation_id']).get('state')))
    report={'pid':os.getpid(),'voice_changes':False,'voice_phase':status.get('phase'),
            'mode':'manual_batch','voice_copied':0,**monitor.report(candidates)}
    progress_status(root,report)
    needs_browser=False
    if status.get('phase')=='idle':
        for item in candidates:
            cid=item['conversation_id']
            if deadline is not None and time.monotonic()>=deadline:break
            if rate_limit_delay(root,monitor):break
            if not monitor.ready(cid,time.time()):continue
            if worker.job(cid).get('state')=='pending':
                needs_browser=True;continue
            report['processing_source']=cid;progress_status(root,report)
            try:
                state=worker.process(item,time.time());monitor.update(cid,time.time(),state=state)
                if state=='deleted':report['voice_copied']+=1
            except BrowserRequired:
                needs_browser=True;break
            except Exception as error:monitor.update(cid,time.time(),error=error)
            if worker.job(cid).get('state')=='pending':
                needs_browser=True
    ordinary={}
    voice_retry=any(v.get('state') in ('voice_busy','temporary_error') for v in monitor.items.values())
    if not needs_browser and not voice_retry:
        from ordinary_archive import batch
        try:ordinary=batch(worker,identities,status,root,deadline=deadline,
                           progress=lambda value:progress_status(root,report,value),monitor=monitor)
        except BrowserRequired:needs_browser=True
    elif voice_retry and not needs_browser:
        ordinary={'deferred':'voice_queue','retry_after':monitor.retry_delay(time.time()),'copied':0,'checked':0}
    report.update(completed_at=time.time(),**monitor.report(candidates))
    if ordinary:report['ordinary']=ordinary
    report['note']='browser_needed' if needs_browser else final_note(root,report,monitor,status)
    report.pop('processing_source',None)
    atomic_json(root/'status.json',report)
    return (needs_browser or bool(ordinary.get('pending'))) and not rate_limit_delay(root,monitor)


def prepare_with_retry(root,controller,limit=360,wait=time.sleep,deadline=None):
    if deadline is None:deadline=time.monotonic()+limit
    totals={'copied':0,'checked':0}
    voice_copied=0
    while not (root/'stop').exists():
        needs_browser=prepare_batch(root,controller,deadline=deadline)
        report=json.loads((root/'status.json').read_text(encoding='utf-8'))
        voice_copied+=report.get('voice_copied',0);report['voice_copied']=voice_copied
        ordinary=report.get('ordinary',{})
        for key in totals:
            totals[key]+=ordinary.get(key,0);ordinary[key]=totals[key]
        atomic_json(root/'status.json',report)
        if needs_browser:
            if time.monotonic()>=deadline:
                deadline_status(root,report,QueueMonitor(root/'observations.json'));return False
            return True
        if not report.get('remaining_sources') and not ordinary.get('retry_after'):return False
        monitor=QueueMonitor(root/'observations.json')
        delay=rate_limit_delay(root,monitor) or (15 if report.get('note')=='voice_active' else ordinary.get('retry_after') or monitor.retry_delay(time.time()))
        remaining=deadline-time.monotonic()
        if delay is None or remaining<=0:return False
        if rate_limit_delay(root,monitor)>=remaining:return False
        report['note']=('waiting_rate_limit' if rate_limit_delay(root,monitor) else
                        'waiting_voice' if report.get('note')=='voice_active' or any(i.get('state')=='voice_busy' for i in monitor.items.values()) else
                        'waiting_connection')
        report['heartbeat_at']=time.time();report.pop('completed_at',None)
        atomic_json(root/'status.json',report)
        wait(min(max(delay,1),remaining,15))
    return False

def run(window,root,once=False,deadline=None):
    from archive_profile import import_archive_login
    controller=Controller();cloud=ArchiveCloud(controller)
    worker=ArchiveWorker(root,cloud,BrowserWriter(window,controller,cloud,root),lambda:read_closed_state(controller,root))
    monitor=QueueMonitor(root/'observations.json')
    if deadline is None:deadline=time.monotonic()+360
    previous=json.loads((root/'status.json').read_text(encoding='utf-8')) if once and (root/'status.json').exists() else {}
    ordinary_copied=previous.get('ordinary',{}).get('copied',0)
    ordinary_checked=previous.get('ordinary',{}).get('checked',0)
    voice_copied=previous.get('voice_copied',0)
    copied_sources=set()
    run_started=time.time()
    ordinary_errors={}
    blocked_projects=set()
    report=previous
    try:
        while not (root/'stop').exists():
            if once and time.monotonic()>=deadline:
                deadline_status(root,report,monitor);break
            status={}
            report={'at':time.time(),'pid':os.getpid(),'mode':'manual_batch' if once else 'background_pc','voice_changes':False,'voice_phase':report.get('voice_phase'),'voice_copied':voice_copied}
            progress_status(root,report,{'copied':ordinary_copied,'checked':ordinary_checked,'pending':0})
            try:
                import_archive_login(window)
                identities,status,routes=read_closed_state(controller,root)
                report['voice_phase']=status.get('phase')
                progress_status(root,report)
                projects={r['project_id'] for r in routes.get('routes',[])}
                candidates=[i for i in identities if i.get('project_session') is True and i.get('conversation_id') and i.get('gizmo_id') in projects and worker.job(i['conversation_id']).get('state')!='deleted']
                candidates.sort(key=lambda i:monitor.priority(i['conversation_id'],worker.job(i['conversation_id']).get('state')))
                candidates=[{**i,'title':worker.job(i['conversation_id']).get('record',{}).get('title') or i.get('title')} for i in candidates]
                if status.get('phase')=='idle':
                    for item in candidates:
                        cid=item['conversation_id']
                        if once and time.monotonic()>=deadline:break
                        if rate_limit_delay(root,monitor):break
                        if not monitor.ready(cid,time.time()):continue
                        if item.get('gizmo_id') in blocked_projects:continue
                        if monitor.priority(cid,worker.job(cid).get('state'))>0 and any(
                            (j:=worker.job(i['conversation_id'])).get('state')=='pending' and
                            time.time()-j.get('stable_since',0)<30 for i in candidates):break
                        report.update(monitor.report(candidates))
                        report.update(processing_source=cid,heartbeat_at=time.time())
                        atomic_json(root/'status.json',report)
                        try:
                            state=worker.process(item,time.time())
                            monitor.update(cid,time.time(),state=state)
                            report['last_job']={'source_id':cid,'state':state}
                        except Exception as error:
                            monitor.update(cid,time.time(),error=error)
                            if isinstance(error,NotSubmitted) and ('draft' in str(error).lower() or 'send control' in str(error)):
                                blocked_projects.add(item.get('gizmo_id'))
                            report['last_error']={'source_id':cid,'type':type(error).__name__,'message':str(error)[:200]}
                            if 'Voice is active or its state is not confirmed' in str(error):
                                status['phase']='active';report['voice_phase']='active'
                                progress_status(root,report);break
                        finally:
                            # A verified copy counts even if deletion confirmation hits 429.
                            job=worker.job(cid)
                            if cid not in copied_sources and job.get('verified_at',0)>=run_started:
                                copied_sources.add(cid);voice_copied+=1;report['voice_copied']=voice_copied
                report.update(monitor.report(candidates))
                from ordinary_archive import batch
                def ordinary_progress(value):
                    key=(value.get('processing_project'),value.get('processing_source'))
                    if value.get('phase')=='checked' and not any((e.get('project_id'),e.get('source_id'))==key for e in value.get('errors',[])):
                        ordinary_errors.pop(key,None)
                    progress_status(root,report,{**value,'copied':ordinary_copied+value['copied'],
                                                'checked':ordinary_checked+value['checked']})
                # Pending voice snapshots must settle and finish before ordinary scans.
                voice_pending=any(i.get('gizmo_id') not in blocked_projects and (
                    monitor.items.get(i['conversation_id'],{}).get('state') in ('voice_busy','temporary_error') or
                    (monitor.ready(i['conversation_id'],time.time()) and worker.job(i['conversation_id']).get('state') in ('pending','copied','submitting','deleting'))) for i in candidates)
                ordinary=({'copied':0,'checked':0,'pending':0,'deferred':'voice_queue'} if voice_pending else
                    batch(worker,identities,status,root,deadline=deadline if once else None,
                          progress=ordinary_progress,monitor=monitor,blocked_projects=blocked_projects))
                for error in ordinary.get('errors',[]):
                    if '429' not in error['detail'] and 'Voice is active' not in error['detail']:
                        ordinary_errors[(error.get('project_id'),error.get('source_id'))]=error
                ordinary_copied+=ordinary.get('copied',0);ordinary_checked+=ordinary.get('checked',0)
                errors={**ordinary_errors,**{(e.get('project_id'),e.get('source_id')):e for e in ordinary.get('errors',[])}}
                report['ordinary']={**ordinary,'copied':ordinary_copied,'checked':ordinary_checked,'errors':list(errors.values())}
                if ordinary.get('deferred')=='voice_active':
                    status['phase']='active';report['voice_phase']='active'
            except Exception as error:
                report['error']={'type':type(error).__name__,'message':str(error)[:200]}
            report['completed_at']=time.time()
            report['note']=final_note(root,report,monitor,status)
            report.pop('processing_source',None)
            atomic_json(root/'status.json',report)
            if once:
                if rate_limit_delay(root,monitor)>=max(0,deadline-time.monotonic()) and rate_limit_delay(root,monitor):break
                ordinary_retry=report.get('ordinary',{}).get('retry_after')
                if report.get('error') or (not report.get('remaining_sources') and not report.get('ordinary',{}).get('pending') and not ordinary_retry):break
                runnable=any(i.get('gizmo_id') not in blocked_projects and monitor.ready(i['conversation_id'],time.time()) and worker.job(i['conversation_id']).get('state') in ('pending','copied','submitting','deleting') for i in candidates)
                retry=ordinary_retry or monitor.retry_delay(time.time()) is not None or status.get('phase')!='idle'
                if not runnable and not retry and not report.get('ordinary',{}).get('pending'):break
                if time.monotonic()>=deadline:break
            for _ in range(15):
                if (root/'stop').exists() or (once and time.monotonic()>=deadline):break
                time.sleep(min(1,max(0,deadline-time.monotonic())) if once else 1)
    finally:
        window.destroy()


def watch_owner(root,pid):
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.windll.kernel32
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x100000,False,pid)
    try:
        while handle and kernel.WaitForSingleObject(handle,1000)==258:pass
    finally:
        if handle:kernel.CloseHandle(handle)
    # Receipts precede every external write/delete, so owner loss can stop immediately.
    try:(root/'stop').touch()
    finally:os._exit(0)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--state',type=Path)
    parser.add_argument('--owner-pid',type=int)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--scheduled',action='store_true')
    args=parser.parse_args();root=args.state or Path(os.environ['LOCALAPPDATA'])/'KasControl'/'Archive'
    root.mkdir(parents=True,exist_ok=True)
    import msvcrt
    lock=(root/'worker.lock').open('a+b')
    try:
        lock.seek(0)
        if lock.read(1)==b'':lock.write(b'0');lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        lock.close();return
    if args.scheduled:
        args.once=True
        (root/'stop').unlink(missing_ok=True)
    if args.owner_pid:threading.Thread(target=watch_owner,args=(root,args.owner_pid),daemon=True).start()
    deadline=time.monotonic()+360 if args.once else None
    if args.once:
        try:
            if not prepare_with_retry(root,Controller(),deadline=deadline):return
        except Exception as error:
            report={}
            try:report=json.loads((root/'status.json').read_text(encoding='utf-8'))
            except (OSError,ValueError):pass
            report.update(pid=os.getpid(),completed_at=time.time(),error={'type':type(error).__name__,'message':str(error)[:200]},note='error')
            atomic_json(root/'status.json',report)
            return
        if time.monotonic()>=deadline:
            deadline_status(root,json.loads((root/'status.json').read_text(encoding='utf-8')),QueueMonitor(root/'observations.json'));return
    import webview
    from webview.platforms import winforms
    # pywebview 6.2.1 implements hidden startup as Show/Hide; never show this worker's form.
    class BackgroundForm(winforms.BrowserView.BrowserForm):
        def Show(self):
            _handle=self.webview.Handle
            self.shown.set()
    winforms.BrowserView.BrowserForm=BackgroundForm
    window=webview.create_window('Kas archive background worker','about:blank',hidden=True,focus=False,js_api=None)
    profile=str(Path(os.environ['LOCALAPPDATA'])/'KasControl'/'ArchiveWebView2')
    webview.start(lambda:run(window,root,args.once,deadline=deadline),gui='edgechromium',debug=False,private_mode=False,storage_path=profile)
    lock.close()


if __name__=='__main__':main()
