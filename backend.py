"""Personal controller; credentials remain in memory and on the station."""
import copy
import hashlib
import json
import os
import re
import shlex
import subprocess
import threading
import time
import uuid

B = '/data/opt/alisa-standalone'
BACKUPS = '/data/opt/kas-control-backups'
SSH = ['wsl.exe', '-d', 'StationLab', '-u', 'stationuser', '--', 'ssh', '-T',
       '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=5',
       '-o', 'ServerAliveCountMax=3', '-F', '/home/stationuser/kas-live/ssh_config', 'station']

class ControlError(Exception):
    pass

def validate_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',value):
        raise ControlError('Некорректный идентификатор проекта или чата.')
    return value

def parse_projects(items):
    result=[]
    for item in items:
        g=item.get('gizmo',{})
        g=g.get('gizmo',g)
        if g.get('is_archived') or not str(g.get('id','')).startswith('g-p-'):
            continue
        result.append({'id':validate_id(g['id']), 'name':g.get('display',{}).get('name') or 'Без названия'})
    return result

def make_settings(original,project,metadata,title):
    validate_id(project)
    if not project.startswith('g-p-'):
        raise ControlError('Выберите проект ChatGPT.')
    if not isinstance(title,str) or len(title.encode('utf-8'))>120 or any(ord(c)<32 for c in title):
        raise ControlError('Название: до 120 байт, без переводов строки и управляющих символов.')
    doc=copy.deepcopy(original)
    if doc.get('version')!=1 or not isinstance(doc.get('chats'),list) or not doc.get('voice'):
        raise ControlError('Неизвестный формат настроек колонки. Изменения отменены.')
    if metadata is not None:
        cid=validate_id(metadata.get('id') or metadata.get('conversation_id'))
        if metadata.get('gizmo_id')!=project or metadata.get('is_archived'):
            raise ControlError('Чат не принадлежит выбранному проекту или находится в архиве.')
        parent=validate_id(metadata.get('current_node'))
        entry=next((c for c in doc['chats'] if c.get('conversation_id')==cid),None)
    else:
        cid='';parent='';entry=None
    if entry is None:
        if len(doc['chats'])>=64:
            raise ControlError('Достигнут лимит 64 привязок. Старые записи автоматически не удаляются.')
        entry={'id':str(uuid.uuid4()),'account_id':'','confirmed':False}
        doc['chats'].append(entry)
    entry.update({'conversation_id':cid,'parent_message_id':parent,'gizmo_id':project,
                  'title':title.strip() or (metadata or {}).get('title') or 'Новый чат Каса'})
    entry.pop('voice_session_id',None)
    if doc.get('selected')!=entry['id']:
        doc['previous']=doc.get('selected','')
        doc['selected']=entry['id']
    return doc,entry

class Station:
    def __init__(self): self.last_settings_hash=None

    def run(self,script,timeout=35):
        try:
            p=subprocess.run(SSH+['sh','-s'],input=script.encode('utf-8'),capture_output=True,
                             timeout=timeout,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except FileNotFoundError:
            raise ControlError('Не найден WSL. Нужен существующий дистрибутив StationLab.') from None
        except subprocess.TimeoutExpired:
            raise ControlError('Превышено время ожидания колонки. Если шло переключение, проверьте состояние и используйте откат.') from None
        if p.returncode:
            marker=next((s for s in p.stdout.decode('utf-8','replace').splitlines() if s.startswith('KAS_ERROR:')),None)
            if marker:
                errors={'STALE':'Настройки изменились во время подготовки. Обновите список и повторите.',
                        'ROLLED_BACK':'Подключение не удалось. Предыдущая привязка восстановлена.',
                        'RECOVERY_FAILED':'Автоматическое восстановление не подтверждено. Нажмите «Вернуть привязку».',
                        'BUSY':'На колонке уже выполняется переключение.',
                        'NO_BACKUP':'Резервной привязки ещё нет.'}
                raise ControlError(errors.get(marker[10:],'Колонка отклонила операцию.'))
            raise ControlError('Нет связи с колонкой или команда не выполнена. Проверьте StationLab и доступность колонки.')
        return p.stdout

    def read_settings(self):
        raw=self.run('cat '+B+'/state/settings.json')
        self.last_settings_hash=hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    def status(self):
        return json.loads(self.run(B+'/bin/alisa-standalone status '+B+'/state'))

    def command(self,action):
        if action not in ('open','close'):
            raise ControlError('Команда не поддерживается.')
        return json.loads(self.run(B+'/bin/alisa-standalone '+action+' '+B+'/state'))

    def install(self,document,expected_hash,was_active):
        if not re.fullmatch('[0-9a-f]{64}',expected_hash or ''):
            raise ControlError('Нет контрольной суммы исходных настроек.')
        backup=BACKUPS+'/'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
        entry=next(c for c in document['chats'] if c['id']==document['selected'])
        restore=restore_script(backup,was_active)
        script=transaction_script(document,expected_hash,backup,restore)
        output=self.run(script,timeout=180)
        state,saved,snapshot_hash=parse_snapshot(output)
        selected=next((c for c in saved['chats'] if c['id']==saved['selected']),{})
        mode=state.get('audio',{}).get('conversation_mode',{})
        if (state.get('phase')!='active' or state.get('error') or
            selected.get('id')!=entry['id'] or not selected.get('conversation_id') or
            mode.get('gizmo_id')!=entry['gizmo_id'] or saved.get('voice')!=document['voice']):
            self.run(guarded_restore_script(backup,snapshot_hash),timeout=150)
            raise ControlError('Новый разговор не подтверждён. Выполнен возврат предыдущей привязки.')
        return {'backup':backup,'conversation_id':selected['conversation_id'],'project_id':selected['gizmo_id']}

    def ensure_open(self,local_id,expected_hash):
        validate_id(local_id)
        if not re.fullmatch('[0-9a-f]{64}',expected_hash or ''):
            raise ControlError('Нет контрольной суммы исходных настроек.')
        state,saved,_=parse_snapshot(self.run(ensure_open_script(local_id,expected_hash),timeout=65))
        if (saved.get('selected')!=local_id or state.get('settings',{}).get('selected')!=local_id
                or state.get('phase')!='active' or state.get('error') or not state.get('agent_connected')):
            raise ControlError('Подключение выбранного чата не подтверждено. Обновите состояние.')

    def rollback(self):
        path=self.run('cat '+BACKUPS+'/LATEST 2>/dev/null || true').decode().strip()
        if not re.fullmatch(re.escape(BACKUPS)+r'/[0-9]{8}-[0-9]{6}-[0-9a-f]{8}',path):
            raise ControlError('Резервной привязки ещё нет.')
        self.run('sh '+path+'/restore.sh',timeout=150)
        return self.status()

def parse_snapshot(output):
    try:
        text=output.decode('utf-8')
        state=json.loads(text.split('KAS_STATE_BEGIN\n',1)[1].split('\nKAS_STATE_END',1)[0])
        raw=text.split('KAS_SETTINGS_BEGIN\n',1)[1].split('\nKAS_SETTINGS_END',1)[0]
        saved=json.loads(raw)
        digest=text.split('KAS_HASH:',1)[1].splitlines()[0]
        if not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError()
        return state,saved,digest
    except (ValueError,IndexError,UnicodeError):
        raise ControlError('Снимок результата не получен. Обновите состояние перед дальнейшими действиями.') from None


def snapshot_script():
    # Capture while the remote switch lock is held; later switches are independent.
    return '\n'.join([
        'printf "KAS_STATE_BEGIN\\n"',
        '"$B/bin/alisa-standalone" status "$B/state"',
        'printf "\\nKAS_STATE_END\\nKAS_SETTINGS_BEGIN\\n"',
        'before=$(sha256sum "$B/state/settings.json" | cut -d " " -f1)',
        'cat "$B/state/settings.json"',
        'printf "\\nKAS_SETTINGS_END\\nKAS_HASH:"',
        'after=$(sha256sum "$B/state/settings.json" | cut -d " " -f1)',
        '[ "$before" = "$after" ] || { echo KAS_ERROR:STALE; exit 1; }',
        'printf "%s\\n" "$after"',
    ])


def guarded_restore_script(backup,expected_hash):
    return f'''set -eu
exec 9>/tmp/kas-control-switch.lock
flock -n 9 || {{ echo KAS_ERROR:BUSY; exit 1; }}
actual=$(sha256sum {B}/state/settings.json | cut -d ' ' -f1)
[ "$actual" = {expected_hash} ] || {{ echo KAS_ERROR:STALE; exit 1; }}
sh {backup}/restore.sh --held-lock
'''


def ensure_open_script(local_id,expected_hash):
    return f'''set -eu
B={B}
exec 9>/tmp/kas-control-switch.lock
flock -n 9 || {{ echo KAS_ERROR:BUSY; exit 1; }}
actual=$(sha256sum "$B/state/settings.json" | cut -d ' ' -f1)
[ "$actual" = {expected_hash} ] || {{ echo KAS_ERROR:STALE; exit 1; }}
state=$("$B/bin/alisa-standalone" status "$B/state")
printf '%s' "$state" | grep -Eq '"selected"[[:space:]]*:[[:space:]]*"{local_id}"' || {{ echo KAS_ERROR:STALE; exit 1; }}
if printf '%s' "$state" | grep -q '"phase": "idle"'; then
 "$B/bin/alisa-standalone" open "$B/state"
else
 printf '%s' "$state" | grep -q '"phase": "active"' || exit 1
fi
n=0
while :; do
 state=$("$B/bin/alisa-standalone" status "$B/state")
 if printf '%s' "$state" | grep -q '"phase": "active"'; then break; fi
 n=$((n+1)); [ "$n" -lt 90 ] || exit 1; sleep .5
done
{snapshot_script()}
'''


def restore_script(backup,was_active):
    # This script deliberately restores settings only, never the voice executable.
    return f'''#!/bin/sh
set -eu
umask 077
B={B}
R={backup}
L=/tmp/kas-control-switch.lock
if [ "${{1:-}}" != --held-lock ]; then
 exec 9>"$L"
 flock -n 9 || {{ echo KAS_ERROR:BUSY; exit 1; }}
fi
"$B/run.sh" stop 9>&-
cp "$R/settings.json" "$B/state/settings.json.restore"
mv "$B/state/settings.json.restore" "$B/state/settings.json"
sync
"$B/run.sh" start 9>&-
n=0
while :; do
 if "$B/bin/alisa-standalone" status "$B/state" 2>/dev/null | grep -q '"agent_connected": true'; then break; fi
 n=$((n+1)); [ "$n" -lt 50 ] || exit 1; sleep .5
done
{('"$B/bin/alisa-standalone" open "$B/state"' if was_active else ':')}
{('n=0; while ! "$B/bin/alisa-standalone" status "$B/state" 2>/dev/null | grep -q \'"phase": "active"\'; do n=$((n+1)); [ "$n" -lt 70 ] || exit 1; sleep .5; done' if was_active else ':')}
sha256sum -c "$R/voice.sha256"
echo KAS_RESTORED
'''

def transaction_script(document,expected_hash,backup,restore):
    payload=shlex.quote(json.dumps(document,ensure_ascii=False))
    return f'''set -eu
umask 077
B={B}
R={backup}
L=/tmp/kas-control-switch.lock
exec 9>"$L"
flock -n 9 || {{ echo KAS_ERROR:BUSY; exit 1; }}
committed=0
prepared=0
cleanup() {{
 trap - EXIT HUP INT TERM
 if [ "$committed" = 0 ] && [ "$prepared" = 1 ]; then
  if sh "$R/restore.sh" --held-lock; then echo KAS_ERROR:ROLLED_BACK; else echo KAS_ERROR:RECOVERY_FAILED; fi
 fi
}}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
actual=$(sha256sum "$B/state/settings.json" | cut -d ' ' -f1)
[ "$actual" = {expected_hash} ] || {{ echo KAS_ERROR:STALE; exit 1; }}
mkdir -p "$R"
cp "$B/state/settings.json" "$R/settings.json"
sha256sum "$B/bin/alisa-standalone" "$B/runtime.env" > "$R/voice.sha256"
printf %s {shlex.quote(restore)} > "$R/restore.sh"
chmod 700 "$R/restore.sh"
printf %s "$R" > {BACKUPS}/LATEST.new
mv {BACKUPS}/LATEST.new {BACKUPS}/LATEST
sync
prepared=1
"$B/run.sh" stop 9>&-
# Graceful shutdown may persist the last response. Preserve it on rollback and
# refuse to overwrite a snapshot that changed during shutdown.
cp "$B/state/settings.json" "$R/settings.json"
actual=$(sha256sum "$B/state/settings.json" | cut -d ' ' -f1)
[ "$actual" = {expected_hash} ] || exit 1
printf %s {payload} > "$B/state/settings.json.new"
mv "$B/state/settings.json.new" "$B/state/settings.json"
sync
"$B/run.sh" start 9>&-
n=0
while :; do
 if "$B/bin/alisa-standalone" status "$B/state" 2>/dev/null | grep -q '"agent_connected": true'; then break; fi
 n=$((n+1)); [ "$n" -lt 50 ] || exit 1; sleep .5
done
"$B/bin/alisa-standalone" open "$B/state"
n=0
while :; do
 if "$B/bin/alisa-standalone" status "$B/state" 2>/dev/null | grep -q '"phase": "active"'; then break; fi
 n=$((n+1)); [ "$n" -lt 70 ] || exit 1; sleep .5
done
sha256sum -c "$R/voice.sha256"
{snapshot_script()}
committed=1
echo KAS_CONNECTED
'''

class Cloud:
    def __init__(self,station): self.station=station;self.http=None

    def request(self,path,params=None):
        from curl_cffi import requests
        if self.http is None:
            auth=json.loads(self.station.run('cat '+B+'/state/web-session.json'))
            self.http=requests.Session(impersonate='chrome')
            self.http.headers.update({'Authorization':'Bearer '+auth['access_token'],
                'User-Agent':auth.get('user_agent','Mozilla/5.0'),'Origin':'https://chatgpt.com','Referer':'https://chatgpt.com/'})
            self.http.cookies.update(auth.get('cookies',{}))
        try:
            r=self.http.get('https://chatgpt.com/backend-api'+path,params=params,timeout=25,allow_redirects=False)
        except Exception:
            raise ControlError('ChatGPT недоступен с ПК. Проверьте интернет и VPN, затем обновите список.') from None
        if r.status_code!=200:
            self.http=None
            if r.status_code in (401,403):
                raise ControlError('ChatGPT отклонил доступ к списку. Обновите список; если ошибка повторяется, проверьте вход клиента колонки.')
            raise ControlError('ChatGPT вернул ошибку '+str(r.status_code)+'. Привязка не изменена.')
        try:return r.json()
        except Exception:raise ControlError('ChatGPT вернул неожиданный формат данных.') from None

    def pages(self,path,params):
        result=[];cursor=None;seen=set()
        for _ in range(100):
            q=dict(params)
            if cursor:q['cursor']=cursor
            d=self.request(path,q)
            if not isinstance(d.get('items'),list):raise ControlError('Неизвестный формат списка ChatGPT.')
            result.extend(d['items']);cursor=d.get('cursor')
            if not cursor:return result
            if not isinstance(cursor,str) or cursor in seen:raise ControlError('ChatGPT повторил страницу списка.')
            seen.add(cursor)
        raise ControlError('Список слишком большой. Получение остановлено без изменений.')

    def projects(self):
        return parse_projects(self.pages('/gizmos/snorlax/sidebar',{'limit':20,'conversations_per_gizmo':0,'owned_only':'true'}))

    def chats(self,project):
        rows=self.pages('/gizmos/'+validate_id(project)+'/conversations',{'limit':50,'owned_only':'true'})
        return [{'id':validate_id(c['id']),'title':c.get('title') or 'Без названия','updated':c.get('update_time','')}
                for c in rows if not c.get('is_archived') and c.get('gizmo_id')==project]

    def conversation(self,cid):
        data=self.request('/conversation/'+validate_id(cid))
        data['id']=cid
        # Do not retain full conversation text in the desktop application.
        return {k:data.get(k) for k in ('id','title','gizmo_id','current_node','is_archived')}

class Controller:
    def __init__(self,station=None,cloud=None):
        self.station=station or Station();self.cloud=cloud or Cloud(self.station)
        self.lock=threading.Lock()

    def overview(self):
        state=self.station.status();doc=self.station.read_settings()
        selected=next((c for c in doc['chats'] if c['id']==doc['selected']),{})
        return {'state':{k:state.get(k) for k in ('pid','phase','error','agent_connected')},
                'selected':{k:selected.get(k) for k in ('id','title','conversation_id','gizmo_id')},'voice':doc['voice']}

    def connect(self,project,cid=None,title='',progress=lambda _:None):
        if not self.lock.acquire(blocking=False):raise ControlError('Переключение уже выполняется.')
        try:
            progress('Проверяю проект и чат…')
            if not any(p['id']==project for p in self.cloud.projects()):raise ControlError('Проект больше недоступен.')
            metadata=self.cloud.conversation(cid) if cid else None
            original=self.station.read_settings()
            make_settings(original,project,metadata,title)  # Validate before closing any session.
            state=self.station.status();was_active=state.get('phase')!='idle'
            selected=next((c for c in original['chats'] if c['id']==original['selected']),{})
            if cid and selected.get('conversation_id')==cid and selected.get('gizmo_id')==project:
                self.station.ensure_open(selected['id'],getattr(self.station,'last_settings_hash',None))
                return {'conversation_id':cid,'project_id':project,'unchanged':True}
            original=self.station.read_settings()
            document,entry=make_settings(original,project,metadata,title)
            progress('Сохраняю откат и подключаю выбранный чат…')
            return self.station.install(document,getattr(self.station,'last_settings_hash',None),was_active)
        finally:self.lock.release()
