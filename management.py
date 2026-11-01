"""Management catalog writes never activate Voice; activation is explicit."""
import copy
import json
import time
import unicodedata
import uuid
import webbrowser

from backend import Controller, ControlError, validate_id
from project_api import create_project
from routes import ROUTER, RouteStore, normalize_phrase


class Manager:
    def __init__(self, controller=None, store=None):
        self.controller = controller or Controller()
        self.station = self.controller.station
        self.cloud = self.controller.cloud
        self.store = store or RouteStore(self.station)

    def _ready(self):
        status = self.store.health()
        features = status.get('features', [])
        if not all(f in features and (not isinstance(features, dict) or features[f])
                   for f in ('catalog', 'lazy_registration')):
            raise ControlError('Обновите обработчик колонки: управление без запуска Voice пока недоступно.')

    def _project(self, project):
        validate_id(project)
        for row in self.cloud.projects():
            if row['id'] == project and project.startswith('g-p-'):
                return row['name']
        raise ControlError('Проект больше недоступен.')

    def _document(self):
        doc = copy.deepcopy(self.store.read())
        doc.setdefault('catalog', [])
        if not isinstance(doc['catalog'], list) or len(doc['catalog']) > 64:
            raise ControlError('Неизвестный формат каталога колонки.')
        return doc

    def _rows(self, project, doc, settings, cloud):
        rows = []
        cloud_ids = {c['id'] for c in cloud}
        local = {c['id']: c for c in settings['chats'] if c.get('gizmo_id') == project}
        seen_local, seen_cloud = set(), set()
        def add(chat_id, cid, title):
            if cid and cid in seen_cloud or chat_id and chat_id in seen_local:
                return
            command = next((r['phrase'] for r in doc['routes']
                            if r['project_id'] == project and
                            (chat_id and r['chat_id'] == chat_id or cid and r.get('conversation_id') == cid)), '')
            rows.append({'key': 'local:' + chat_id if chat_id else 'cloud:' + cid,
                         'title': title or 'Новый чат', 'project_id': project,
                         'conversation_id': cid, 'local_id': chat_id,
                         'draft': not cid or cid not in cloud_ids, 'command': command,
                         'current': bool(chat_id) and settings.get('selected') == chat_id})
            if cid: seen_cloud.add(cid)
            if chat_id: seen_local.add(chat_id)
        for entry in doc['catalog']:
            if entry['project_id'] != project: continue
            saved = local.get(entry['chat_id'], {})
            add(entry['chat_id'], saved.get('conversation_id') or entry.get('conversation_id', ''), entry['title'])
        for entry in local.values():
            add(entry['id'], entry.get('conversation_id', ''), entry.get('title'))
        for entry in cloud:
            add('', entry['id'], entry['title'])
        return rows

    def list_chats(self, project):
        self._project(project)
        return self._rows(project, self._document(), self.station.read_settings(), self.cloud.chats(project))

    def bootstrap(self):
        projects = self.cloud.projects()
        doc = self._document()
        settings = self.station.read_settings()
        state = self.station.status()
        selected = next((c for c in settings['chats'] if c['id'] == settings.get('selected')), {})
        entry = next((c for c in doc['catalog'] if c['chat_id'] == selected.get('id')), {})
        try:
            self._ready(); ready = True
        except ControlError:
            ready = False
        names = {p['id']: p['name'] for p in projects}
        catalog = {c['chat_id']: c for c in doc['catalog']}
        routes = [{**r, 'title': catalog.get(r['chat_id'], {}).get('title', r.get('title', 'Без названия')),
                   'project_name': names.get(r['project_id'], r.get('project_name', 'Проект'))} for r in doc['routes']]
        return {'projects': projects, 'current': {
            'project_id': selected.get('gizmo_id', ''), 'key': 'local:' + selected['id'] if selected else '',
            'chat_id': selected.get('id', ''), 'title': entry.get('title') or selected.get('title', ''),
            'conversation_id': selected.get('conversation_id', ''), 'phase': state.get('phase'),
            'voice': settings.get('voice')}, 'router_ready': ready,
            'native_commands': [{'action': a, 'phrase': p} for a, p in self.store.native_commands()], 'routes': routes}

    def create_project(self, name):
        self._ready()
        return create_project(self.cloud, name)

    def create_chat(self, project, title, phrase=''):
        self._ready()
        name = self._project(project)
        if (not isinstance(title, str) or not title.strip() or len(title.encode('utf-8', 'replace')) > 120
                or any(unicodedata.category(c).startswith('C') for c in title)):
            raise ControlError('Название чата: до 120 байт, без управляющих символов.')
        doc = self._document()
        if len(doc['catalog']) >= 64: raise ControlError('Достигнут лимит 64 записей каталога.')
        entry = {'chat_id': str(uuid.uuid4()), 'project_id': project, 'conversation_id': '',
                 'parent_message_id': '', 'title': title.strip(), 'project_name': name}
        if not isinstance(phrase,str):raise ControlError('Введите фразу вызова текстом.')
        if phrase.strip():doc['routes']=self._command_routes(doc,entry,phrase)
        doc['catalog'].append(entry)
        self.store.write(doc)
        return self._rows(project, doc, {'chats': []}, [])[-1]

    def _resolve(self, project, key, doc):
        name = self._project(project)
        if not isinstance(key, str) or ':' not in key: raise ControlError('Выберите чат.')
        kind, ident = key.split(':', 1)
        validate_id(ident)
        settings = self.station.read_settings()
        if kind == 'local':
            entry = next((copy.deepcopy(c) for c in doc['catalog'] if c['chat_id'] == ident and c['project_id'] == project), None)
            saved = next((c for c in settings['chats'] if c['id'] == ident and c.get('gizmo_id') == project), None)
            if entry is None and saved is None: raise ControlError('Чат не принадлежит выбранному проекту.')
            if entry is None:
                entry = {'chat_id': ident, 'project_id': project, 'title': saved.get('title') or 'Новый чат'}
            cid = (saved or {}).get('conversation_id') or entry.get('conversation_id', '')
        elif kind == 'cloud':
            cid = ident
            entry = next((copy.deepcopy(c) for c in doc['catalog'] if c.get('conversation_id') == cid and c['project_id'] == project), None)
            saved = next((c for c in settings['chats'] if c.get('conversation_id') == cid and c.get('gizmo_id') == project), {})
            if entry is None:
                entry = {'chat_id': saved.get('id') or str(uuid.uuid4()), 'project_id': project,
                         'title': saved.get('title') or ''}
        else: raise ControlError('Неизвестный вид чата.')
        parent = (saved or {}).get('parent_message_id') or entry.get('parent_message_id', '')
        # A newly started native chat can remain absent from cloud history until speech.
        if cid and not (kind == 'local' and (saved or not parent)):
            metadata = self.cloud.conversation(validate_id(cid))
            if metadata.get('gizmo_id') != project or metadata.get('is_archived'):
                raise ControlError('Чат не принадлежит выбранному проекту или находится в архиве.')
            parent = validate_id(metadata.get('current_node'))
            entry['title'] = entry.get('title') or metadata.get('title') or 'Новый чат'
        elif parent:
            validate_id(parent)
        entry.update(conversation_id=cid, parent_message_id=parent, project_name=name)
        return entry

    def _put(self, doc, entry):
        rows = [c for c in doc['catalog'] if c['chat_id'] != entry['chat_id']]
        if len(rows) >= 64: raise ControlError('Достигнут лимит 64 записей каталога.')
        doc['catalog'] = rows + [entry]

    def _command_routes(self,doc,entry,phrase):
        phrase = normalize_phrase(phrase)
        for action, text in self.store.native_commands():
            if action != 1 and normalize_phrase(text) == phrase:
                raise ControlError('Эта фраза занята штатной командой.')
        for route in doc['routes']:
            if normalize_phrase(route['phrase']) == phrase and route['chat_id'] != entry['chat_id']:
                raise ControlError('Эта команда уже назначена другому чату.')
        routes = [r for r in doc['routes'] if r['chat_id'] != entry['chat_id']]
        if len(routes) >= 64: raise ControlError('Достигнут лимит 64 голосовых команд.')
        return routes + [{**entry, 'phrase': phrase}]

    def save_command(self, project, key, phrase):
        self._ready()
        doc = self._document()
        entry = self._resolve(project, key, doc)
        routes=self._command_routes(doc,entry,phrase)
        self._put(doc, entry)
        doc['routes'] = routes
        self.store.write(doc)
        return {'phrase': routes[-1]['phrase']}

    def remove_command(self, project, key):
        self._ready()
        doc = self._document()
        # Removal remains possible when a formerly saved cloud chat is now archived.
        self._project(project)
        rows = self._rows(project, doc, self.station.read_settings(), [])
        selected = next((c for c in rows if c['key'] == key), None)
        if selected is None: raise ControlError('Выберите зарегистрированный чат.')
        doc['routes'] = [r for r in doc['routes'] if not (r['project_id'] == project and r['chat_id'] == selected['local_id'])]
        self.store.write(doc)
        return {}

    def connect_chat(self, project, key):
        self._ready()
        doc = self._document()
        entry = self._resolve(project, key, doc)
        self._put(doc, entry)
        self.store.write(doc)
        # Keep the router's structured failure; SSH transport failures still raise.
        raw = self.station.run(ROUTER + '/router open ' + validate_id(entry['chat_id']) + ' || true', timeout=180)
        try:
            result = json.loads(raw)
        except (ValueError, TypeError, UnicodeError):
            raise ControlError('Подключение не подтверждено. Обновите состояние перед повторной попыткой.') from None
        if not isinstance(result, dict) or result.get('ok') is not True:
            error = result.get('error') if isinstance(result, dict) else None
            if isinstance(error, str):
                if 'recovery incomplete' in error:
                    raise ControlError('Подключение не удалось; восстановление не завершено. Требуется восстановить прежнюю привязку и проверить состояние колонки.')
                if 'previous settings restored' in error:
                    raise ControlError('Подключение не удалось. Прежняя привязка автоматически восстановлена; обновите состояние.')
            raise ControlError('Подключение не подтверждено. Обновите состояние.')
        return result

    def close_session(self):
        uncertain = 'Закрытие сеанса не подтверждено. Обновите состояние перед повторной командой.'
        try:
            self.station.command('close')
            deadline = time.monotonic() + 18
            while time.monotonic() < deadline:
                state = self.station.status()
                if state.get('error'):
                    raise ControlError(uncertain)
                if state.get('phase') == 'idle':
                    return state
                time.sleep(.3)
        except Exception:
            raise ControlError(uncertain) from None
        raise ControlError(uncertain)

    def restore_binding(self):
        return self.station.rollback()

    def open_in_chatgpt(self, project, key):
        entry = self._resolve(project, key, self._document())
        cid = entry['conversation_id']
        if not cid: raise ControlError('Чат ещё не запущен и не сохранён в ChatGPT.')
        metadata = self.cloud.conversation(cid)
        if metadata.get('gizmo_id') != project or metadata.get('is_archived'):
            raise ControlError('Сохранённый чат недоступен в выбранном проекте.')
        url = 'https://chatgpt.com/c/' + validate_id(cid)
        if not webbrowser.open(url): raise ControlError('Не удалось открыть браузер.')
        return {'url': url}
