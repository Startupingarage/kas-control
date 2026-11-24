"""User-triggered local report: never include credentials or conversation content."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid
import zipfile

BUILD='2026.11.13-opt1'
COUNTERS=('received','sent','alsa_underruns','jitter_ms','max_arrival_gap_ms','loop_overruns',
          'interruptions','idle_restarts','render_failure_events','render_failure_blocks')
NETEQ=('packets','concealed_samples','silent_concealed_samples','total_samples','interruption_count',
       'interruption_duration_ms','mean_delay_ms','current_delay_ms','target_delay_ms','discarded_packets')

def report(snapshot,archive):
    state=snapshot['state'];audio=state.get('audio') or state.get('last_call') or {}
    numeric=lambda data,keys:{k:data[k] for k in keys if type(data.get(k)) in (int,float,bool)}
    result={'build':BUILD,'created_at':time.time(),'voice_pid':state.get('pid'),'phase':state.get('phase'),
            'voice':snapshot['settings'].get('voice'),'agent_connected':bool(state.get('agent_connected')),
            'voice_error_present':bool(state.get('error')),'router_ready':bool(snapshot['health'].get('ready')),
            'audio':numeric(audio,COUNTERS),'neteq':numeric(audio.get('neteq',{}),NETEQ),
            'archive':numeric(archive,('running','hourly','next_run_at','remaining','blocked','updated'))}
    if getattr(sys,'frozen',False):
        h=hashlib.sha256()
        with open(sys.executable,'rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
        result['executable_sha256']=h.hexdigest()
    return result

def save(snapshot,archive):
    root=Path(os.environ['LOCALAPPDATA'])/'KasControl/Diagnostics';root.mkdir(parents=True,exist_ok=True)
    path=root/('kas-diagnostics-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]+'.zip')
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr('report.json',json.dumps(report(snapshot,archive),ensure_ascii=False,indent=2))
        package.writestr('README.txt','Local diagnostic report. No conversation text or credentials are included.\nInterruptions counts forced client stops, not all natural voice interruptions.\nConcealment includes silence; counters do not prove model identity or perceived voice quality.\n')
    return {'path':str(path),'build':BUILD}
