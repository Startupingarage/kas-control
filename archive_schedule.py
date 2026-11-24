"""Twice-daily archive lifetime belongs to the tray application."""
import json
import os
from pathlib import Path
import threading
import time
import math
from backend import ControlError


def preference_path():
    return Path(os.environ['LOCALAPPDATA'])/'KasControl/archive-mode.json'


def enabled():
    path=preference_path()
    return json.loads(path.read_text(encoding='utf-8')).get('hourly',False) if path.exists() else False


def timer_status():
    try:
        status=json.loads(preference_path().with_name('archive-timer.json').read_text(encoding='utf-8'))
        value=status.get('automatic_error','') if isinstance(status,dict) else ''
        result={'automatic_error':value[:200] if isinstance(value,str) else ''}
        due=status.get('next_run_at') if isinstance(status,dict) else None
        if type(due) in (int,float) and math.isfinite(due) and due>0:result['next_run_at']=due
        return result
    except (OSError,ValueError):return {'automatic_error':''}

def timer_error():return timer_status()['automatic_error']


def set_enabled(value):
    from archive_worker import atomic_json
    if value not in ('twice_daily','hourly','manual'):raise ControlError('Неизвестный режим архивации.')
    path=preference_path();path.parent.mkdir(parents=True,exist_ok=True)
    # Keep the existing enabled flag readable by rollback builds.
    atomic_json(path,{'hourly':value!='manual'})
    return enabled()


class AppArchiveTimer:
    def __init__(self,archive,interval=43200):
        self.archive=archive;self.interval=interval
        self.changed=threading.Event();self.stopped=threading.Event()
        self.thread=threading.Thread(target=self.run,daemon=True,name='KasArchiveTimer')
        self.last_error=''
        self.next_run_at=time.time()+interval
        try:
            saved=json.loads(preference_path().with_name('archive-timer.json').read_text(encoding='utf-8'))
            if (saved.get('interval_seconds')==interval and type(saved.get('next_run_at')) in (int,float)
                    and math.isfinite(saved['next_run_at'])):
                self.next_run_at=saved['next_run_at']
        except (OSError,ValueError,AttributeError):pass

    def _publish_error(self):
        path=preference_path().with_name('archive-timer.json')
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            temporary=path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'automatic_error':self.last_error,'next_run_at':self.next_run_at,
                                             'interval_seconds':self.interval}),encoding='utf-8')
            os.replace(temporary,path)
        except OSError:pass

    def start(self):
        self._publish_error();self.thread.start()

    def run(self):
        while not self.stopped.is_set():
            if self.changed.wait(min(60,max(0,self.next_run_at-time.time()))):
                self.changed.clear();continue
            if self.stopped.is_set():break
            if time.time()<self.next_run_at:continue
            # Missed runs collapse to one attempt; never replay a backlog on startup.
            self.next_run_at=time.time()+self.interval
            try:
                if enabled():self.archive.start()
                self.last_error=''
            except Exception as error:self.last_error=str(error)[:200]
            self._publish_error()

    def stop(self):
        self.stopped.set();self.changed.set()
        if self.thread.is_alive():self.thread.join(timeout=10)
