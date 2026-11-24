"""Pace conversation reads shared by archive preflight, writer and panel transport."""
import threading
import time
import json
import math
import os
from pathlib import Path
import tempfile
from email.utils import parsedate_to_datetime
from contextlib import contextmanager


def cooldown_path(root=None):return (Path(root) if root else Path(os.environ['LOCALAPPDATA'])/'KasControl/Archive')/'ordinary-rate-limit.json'

def cooldown_remaining(root=None):
    try:
        value=json.loads(cooldown_path(root).read_text(encoding='utf-8')).get('retry_at',0)
        return max(0,value-time.time()) if type(value) in (int,float) and math.isfinite(value) else 0
    except (OSError,ValueError,AttributeError):return 0

_cooldown_lock=threading.Lock()

def record_rate_limit(value=None,root=None):
    import msvcrt
    path=cooldown_path(root);path.parent.mkdir(parents=True,exist_ok=True)
    with _cooldown_lock,(path.parent/'rate-limit.lock').open('a+b') as lock:
        lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_LOCK,1)
        try:_record_rate_limit(value,root)
        finally:
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)

def _record_rate_limit(value=None,root=None):
    try:
        delay=float(value)
        if not math.isfinite(delay):raise ValueError()
    except (ValueError,TypeError):
        try:delay=parsedate_to_datetime(value).timestamp()-time.time()
        except (ValueError,TypeError,AttributeError):delay=300
    delay=max(4,delay,cooldown_remaining(root))
    path=cooldown_path(root);path.parent.mkdir(parents=True,exist_ok=True)
    # Separate temp names allow UI and worker to publish a pause without corrupting JSON.
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,prefix='.rate-limit-',delete=False) as stream:
        temporary=Path(stream.name);json.dump({'retry_at':time.time()+delay},stream)
    try:os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)

def require_cloud_ready():
    remaining=cooldown_remaining()
    if remaining:
        from backend import ControlError
        raise ControlError('ChatGPT ограничил запросы. Повторите обновление через '+str(max(1,math.ceil(remaining)))+' с. Команды колонки доступны.')


class ChatReadPacer:
    def __init__(self,clock=time.monotonic,sleep=time.sleep):
        self.clock=clock;self.sleep=sleep;self.next_read=0;self.lock=threading.Lock()

    def wait(self):
        with self.lock:
            delay=max(0,self.next_read-self.clock())
            if delay:self.sleep(delay)
            # Observed conversation-read HTTP 429 requests Retry-After: 3.
            self.next_read=self.clock()+4

    def defer(self,seconds):
        try:delay=max(4,float(seconds))
        except (TypeError,ValueError):delay=30
        with self.lock:self.next_read=max(self.next_read,self.clock()+delay)


class SharedChatReadPacer:
    """Reserve read starts across UI and worker; never hold the file lock while sleeping."""
    def __init__(self,root=None,interval=65,clock=time.time,sleep=time.sleep):
        self.root=root;self.interval=interval;self.clock=clock;self.sleep=sleep

    @contextmanager
    def locked(self):
        import msvcrt
        path=cooldown_path(self.root).with_name('conversation-read-pacer.json')
        path.parent.mkdir(parents=True,exist_ok=True)
        with _cooldown_lock,(path.parent/'conversation-read-pacer.lock').open('a+b') as lock:
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_LOCK,1)
            try:yield path
            finally:
                lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)

    def read(self,path):
        if not path.exists():return 0
        value=json.loads(path.read_text(encoding='utf-8'))['next_read']
        if type(value) not in (int,float) or not math.isfinite(value):
            raise ValueError('Invalid shared conversation request deadline')
        return value

    def write(self,path,value):
        from archive_worker import atomic_json
        atomic_json(path,{'next_read':value})

    def wait(self):
        while True:
            with self.locked() as path:
                now=self.clock();delay=self.read(path)-now
                if delay<=0:
                    # Conversation reads returned 429 at 4, 22 and 30 second spacing.
                    self.write(path,now+self.interval)
                    return
            self.sleep(delay)

    def defer(self,seconds):
        try:
            delay=float(seconds)
            if not math.isfinite(delay):raise ValueError()
            delay=max(self.interval,delay)
        except (TypeError,ValueError):delay=30
        with self.locked() as path:
            self.write(path,max(self.read(path),self.clock()+delay))


conversation_reads=SharedChatReadPacer()

def pace_chat_read():conversation_reads.wait()

def defer_chat_reads(seconds):conversation_reads.defer(seconds)
