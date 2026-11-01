"""Station-owned routing configuration; no voice configuration writes."""
import copy
import hashlib
import json
import re
import shlex
import time
from backend import B, ControlError, validate_id

ROUTER='/data/opt/kas-command-router'

def normalize_phrase(value):
    if not isinstance(value,str) or len(value)>200 or any(ord(c)<32 for c in value):
        raise ControlError('Команда: от 2 до 200 символов, без переводов строки.')
    words=re.sub(r'[^a-zа-я0-9]+',' ',value.lower().replace('ё','е')).split()
    if words and words[0] in ('алиса','яндекс'):words.pop(0)
    if words and words[-1]=='пожалуйста':words.pop()
    result=' '.join(words)
    if len(result)<2:raise ControlError('Введите команду после слова «Алиса».')
    return result

def update_routes(document,phrase,chat,native,project_name):
    phrase=normalize_phrase(phrase)
    for action,text in native:
        if action!=1 and normalize_phrase(text)==phrase:
            raise ControlError('Эта фраза занята штатной командой. Выберите другую.')
    entry={'phrase':phrase,'chat_id':validate_id(chat.get('id')),
           'project_id':validate_id(chat.get('gizmo_id')),
           'conversation_id':validate_id(chat.get('conversation_id')),
           'title':chat.get('title') or 'Без названия','project_name':project_name}
    result=copy.deepcopy(document)
    if result.get('version')!=1 or not isinstance(result.get('routes'),list):
        raise ControlError('Неизвестный формат команд колонки.')
    for route in result['routes']:
        if normalize_phrase(route['phrase'])==phrase and route['chat_id']!=entry['chat_id']:
            raise ControlError('Эта команда уже назначена другому чату. Сначала удалите прежнюю привязку.')
    result['routes']=[r for r in result['routes'] if r['chat_id']!=entry['chat_id']]
    if len(result['routes'])>=64:raise ControlError('Достигнут лимит 64 голосовых команд.')
    result['routes'].append(entry)
    return result

def merge_chats(cloud,local,project):
    result=[dict(c) for c in cloud];seen={c['id'] for c in result}
    for chat in local:
        cid=chat.get('conversation_id')
        if chat.get('gizmo_id')==project and cid and cid not in seen:
            result.append({'id':cid,'title':chat.get('title') or 'Новый чат','pending':True})
            seen.add(cid)
    return result

class RouteStore:
    def __init__(self,station):self.station=station;self.hash=None

    def read(self):
        raw=self.station.run(f'if [ -f {ROUTER}/routes.json ]; then cat {ROUTER}/routes.json; else printf \'{{"version":1,"routes":[]}}\'; fi')
        self.hash=hashlib.sha256(raw).hexdigest()
        document=json.loads(raw)
        if document.get('version')!=1 or not isinstance(document.get('routes'),list):
            raise ControlError('Неизвестный формат команд колонки.')
        return document

    def native_commands(self):
        raw=self.station.run(f'cat {B}/commands.txt').decode('utf-8')
        result=[]
        for line in raw.splitlines():
            row=line.split('\t',1)
            if len(row)==2 and row[0] in ('1','2','3','4'):result.append((int(row[0]),row[1]))
        return result

    def health(self):
        raw=self.station.run(f'date +%s; cat {ROUTER}/status.json 2>/dev/null || printf \'{{}}\'').decode('utf-8')
        now,body=raw.split('\n',1);status=json.loads(body)
        if not status.get('ready') or not status.get('connected') or not 0<=int(now)-status.get('updated_unix',0)<=5:
            raise ControlError('Обработчик команд колонки не готов. Команда не назначена; голос не изменён.')
        return status

    def write(self,document):
        if not re.fullmatch('[0-9a-f]{64}',self.hash or ''):raise ControlError('Сначала обновите команды.')
        payload=json.dumps(document,ensure_ascii=False)
        script=f'''set -eu
umask 077
R={ROUTER}
[ -f "$R/routes.json" ] || {{ echo KAS_ERROR:ROUTER_MISSING; exit 1; }}
exec 9>/tmp/kas-control-switch.lock
flock -n 9 || {{ echo KAS_ERROR:BUSY; exit 1; }}
actual=$(sha256sum "$R/routes.json" | cut -d ' ' -f1)
[ "$actual" = {self.hash} ] || {{ echo KAS_ERROR:STALE; exit 1; }}
cp "$R/routes.json" "$R/routes.previous.json"
printf %s {shlex.quote(payload)} > "$R/routes.json.new"
mv "$R/routes.json.new" "$R/routes.json"
sync
'''
        self.station.run(script)
        observed=self.read()
        if observed!=document:raise ControlError('Не удалось подтвердить сохранение команд. Обновите список.')
        expected=hashlib.sha256(payload.encode('utf-8')).hexdigest()
        for _ in range(8):
            try:
                status=self.health()
                if status.get('config_sha256')==expected:return observed
            except ControlError:pass
            time.sleep(.5)
        # Restore only our failed write, never overwrite a subsequent user's edit.
        self.station.run(f'''set -eu
R={ROUTER}
exec 9>/tmp/kas-control-switch.lock
flock -n 9 || {{ echo KAS_ERROR:BUSY; exit 1; }}
actual=$(sha256sum "$R/routes.json" | cut -d ' ' -f1)
[ "$actual" = {expected} ] || {{ echo KAS_ERROR:STALE; exit 1; }}
cp "$R/routes.previous.json" "$R/routes.json.restore"
mv "$R/routes.json.restore" "$R/routes.json"
sync
''')
        raise ControlError('Обработчик не подтвердил команду. Предыдущий файл команд восстановлен; обновите состояние.')

    def assign(self,controller,project,cid,phrase,progress=lambda _:None):
        # Validate conflicts before a first-time chat registration reconnects Voice.
        self.health()
        document=self.read();native=self.native_commands()
        projects=controller.cloud.projects()
        name=next((p['name'] for p in projects if p['id']==project),None)
        if name is None:raise ControlError('Проект больше недоступен.')
        settings=self.station.read_settings()
        entry=next((c for c in settings['chats'] if c.get('conversation_id')==cid and c.get('gizmo_id')==project),None)
        provisional=entry or {'id':'pending-registration','gizmo_id':project,'conversation_id':cid}
        update_routes(document,phrase,provisional,native,name)
        metadata=controller.cloud.conversation(cid) # Empty, unsaved conversations cannot receive a durable route.
        if metadata.get('gizmo_id')!=project or metadata.get('is_archived'):
            raise ControlError('Чат не принадлежит выбранному проекту или находится в архиве.')
        registered_now=entry is None
        if registered_now:
            progress('Первое подключение этого чата: сохраняю откат и регистрирую на колонке…')
            controller.connect(project,cid,progress=progress)
            settings=self.station.read_settings()
            entry=next((c for c in settings['chats'] if c.get('conversation_id')==cid and c.get('gizmo_id')==project),None)
        if entry is None:raise ControlError('Колонка не зарегистрировала чат.')
        try:
            state=self.station.status()
            if not any(c.get('id')==entry['id'] and c.get('gizmo_id')==project for c in state.get('settings',{}).get('chats',[])):
                raise ControlError('Чат ещё не загружен голосовым клиентом. Подключите его перед назначением команды.')
            self.write(update_routes(document,phrase,entry,native,name))
        except ControlError as error:
            if registered_now:
                raise ControlError('Чат подключён, но команда не подтверждена. Можно вернуть прежнюю привязку. '+str(error)) from None
            raise
        return {'phrase':normalize_phrase(phrase)}

    def remove(self,cid):
        document=self.read()
        document['routes']=[r for r in document['routes'] if r.get('conversation_id')!=cid]
        return self.write(document)
