"""Manual archive lifetime belongs to the desktop window."""
import os
from pathlib import Path
import subprocess
import sys
import time
import threading
from backend import ControlError

class ArchiveControl:
    def __init__(self):
        self.root=Path(os.environ['LOCALAPPDATA'])/'KasControl/Archive'
        self.process=None;self.started_at=0
        self._start_guard=threading.Lock()

    @property
    def running(self):return self.process is not None and self.process.poll() is None

    @property
    def working(self):
        if self.running:return True
        if not (self.root/'worker.lock').exists():return False
        import msvcrt
        with (self.root/'worker.lock').open('r+b') as lock:
            try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:return True
            msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
        return False

    def request_stop(self):
        with self._start_guard:
            if self.running:self._stop()
            elif self.working:(self.root/'stop').touch()

    def start(self):
        with self._start_guard:self._start()

    def _start(self):
        if self.working:return
        if getattr(sys,'frozen',False):
            command=[str(Path(sys.executable).parent/'KasArchiveWorker.exe')]
        else:command=[sys.executable,str(Path(__file__).parent/'archive_desktop.py')]
        self.root.mkdir(parents=True,exist_ok=True)
        (self.root/'stop').unlink(missing_ok=True)
        self.started_at=time.time()
        try:
            with (self.root/'worker.log').open('ab') as log:
                self.process=subprocess.Popen(command+['--state',str(self.root),'--owner-pid',str(os.getpid()),'--once'],
                    stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except OSError:raise ControlError('Не удалось запустить архиватор. Проверьте наличие KasArchiveWorker.exe рядом с пультом.') from None

    def stop(self):
        with self._start_guard:self._stop()

    def _stop(self):
        if not self.running:return
        (self.root/'stop').touch()
        try:self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            subprocess.run(['taskkill.exe','/PID',str(self.process.pid),'/T','/F'],
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=10)
        self.process=None
