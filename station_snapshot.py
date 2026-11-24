"""One read-only SSH round trip, with disk/memory revision checks."""
import hashlib
import json
import re
import time
import uuid

from backend import B,ControlError


def management_snapshot(station):
    for attempt in range(3):
        try:return _snapshot_once(station)
        except ControlError as error:
            if attempt==2 or str(error) not in ('Настройки изменились во время подготовки. Обновите список и повторите.',
                                               'Состояние колонки изменилось или неполно. Обновите подключение.'):
                raise
            time.sleep(.08)


def _snapshot_once(station):
    marker='KAS_SNAPSHOT_'+uuid.uuid4().hex
    names=('now','settings','state','routes','commands','health','settings_hash','routes_hash')
    router='/data/opt/kas-command-router'
    commands=('date +%s',f'cat {B}/state/settings.json',f'{B}/bin/alisa-standalone status {B}/state',
              f'cat {router}/routes.json',f'cat {B}/commands.txt',
              f'cat {router}/status.json 2>/dev/null || printf \'{{}}\'',
              'printf %s "$S"','printf %s "$R"')
    script=f"set -eu\nM='{marker}'\nS=$(sha256sum {B}/state/settings.json | cut -d ' ' -f1)\nR=$(sha256sum {router}/routes.json | cut -d ' ' -f1)\n"
    for command in commands:script+='printf "\\n%s\\n" "$M"\n'+command+'\n'
    script+=f'''[ "$S" = "$(sha256sum {B}/state/settings.json | cut -d ' ' -f1)" ] &&
[ "$R" = "$(sha256sum {router}/routes.json | cut -d ' ' -f1)" ] || {{ printf '\\nKAS_ERROR:STALE\\n'; exit 1; }}
printf "\\n%s\\n" "$M"
'''
    raw=station.run(script)
    try:
        parts=raw.decode('utf-8').split('\n'+marker+'\n')
        if len(parts)!=len(names)+2 or parts[0].strip() or parts[-1].strip():raise ValueError()
        result=dict(zip(names,(x.strip() for x in parts[1:-1])))
        for key in ('settings','state','routes','health'):result[key]=json.loads(result[key])
        result['now']=int(result['now'])
        if not all(re.fullmatch('[0-9a-f]{64}',result[k]) for k in ('settings_hash','routes_hash')):raise ValueError()
        revision=hashlib.sha256(json.dumps(result['settings'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if result['state'].get('settings_revision')!=revision:raise ValueError()
        if result['routes'].get('version')!=1 or not isinstance(result['routes'].get('routes'),list):raise ValueError()
        result['commands']=[(int(a),p) for line in result['commands'].splitlines()
                            if len(row:=line.split('\t',1))==2 for a,p in [row] if a in ('1','2','3','4')]
        station.last_settings_hash=result['settings_hash']
        return result
    except (ValueError,TypeError,KeyError,AttributeError):
        raise ControlError('Состояние колонки изменилось или неполно. Обновите подключение.') from None
