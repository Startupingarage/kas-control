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
        self._projects=[]

    def _ready(self):
        status = self.store.health()
        features = status.get('features', [])
        if not all(f in features and (not isinstance(features, dict) or features[f])
                   for f in ('catalog', 'lazy_registration')):
            raise ControlError('Обновите обработчик колонки: управление без запуска Voice пока недоступно.')

    def set_voice(self,voice):
        return self.station.set_voice(voice)

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
        return self._bootstrap(self.cloud_projects(),self.station.management_snapshot())

    def cloud_projects(self):
        projects=self.cloud.projects();self._projects=projects
        return projects

    def bootstrap_local(self):
        snapshot=self.station.management_snapshot();doc=snapshot['routes']
        projects={p['id']:p for p in self._projects}
        for entry in doc.get('catalog',[]):
            project=entry.get('project_id')
            if project and project not in projects:
                projects[project]={'id':validate_id(project),'name':entry.get('project_name') or entry.get('title') or 'Проект'}
        return self._bootstrap(list(projects.values()),snapshot)

    def _bootstrap(self,projects,snapshot):
        doc=snapshot['routes'];doc.setdefault('catalog',[])
        if not isinstance(doc['catalog'],list) or len(doc['catalog'])>64:raise ControlError('Неизвестный каталог колонки.')
        self.store.hash=snapshot['routes_hash']
        settings=snapshot['settings'];state=snapshot['state']
        selected = next((c for c in settings['chats'] if c['id'] == settings.get('selected')), {})
        entry = next((c for c in doc['catalog'] if c['chat_id'] == selected.get('id')), {})
        health=snapshot['health']
        ready=bool(health.get('ready') and health.get('connected') and 0<=snapshot['now']-health.get('updated_unix',0)<=5
                   and all(f in health.get('features',[]) for f in ('catalog','lazy_registration')))
        names = {p['id']: p['name'] for p in projects}
        catalog = {c['chat_id']: c for c in doc['catalog']}
        routes = [{**r, 'title': catalog.get(r['chat_id'], {}).get('title', r.get('title', 'Без названия')),
                   'project_name': names.get(r['project_id'], r.get('project_name', 'Проект'))} for r in doc['routes']]
        return {'project_sessions': bool(doc.get('project_sessions')), 'project_status': self.project_status(settings, state, doc), 'projects': projects, 'current': {
            'project_id': selected.get('gizmo_id', ''), 'key': 'local:' + selected['id'] if selected else '',
            'chat_id': selected.get('id', ''), 'title': entry.get('title') or selected.get('title', ''),
            'conversation_id': selected.get('conversation_id', ''), 'phase': state.get('phase'),
            'voice': settings.get('voice')}, 'router_ready': ready,
            'native_commands': [{'action': a, 'phrase': p} for a, p in snapshot['commands']], 'routes': routes}

    def create_project(self, name):
        self._ready()
        project=create_project(self.cloud, name)
        self._projects=[p for p in self._projects if p['id']!=project['id']]+[project]
        return project

    def create_chat(self, project, title, phrase=''):
        if self._document().get('project_sessions'): raise ControlError('В режиме проектов используйте команду проекта и новый разговор.')
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
        if self._document().get('project_sessions'): raise ControlError('В режиме проектов используйте команду проекта и новый разговор.')
        self._ready()
        doc = self._document()
        entry = self._resolve(project, key, doc)
        routes=self._command_routes(doc,entry,phrase)
        self._put(doc, entry)
        doc['routes'] = routes
        self.store.write(doc)
        return {'phrase': routes[-1]['phrase']}

    def remove_command(self, project, key):
        if self._document().get('project_sessions'): raise ControlError('В режиме проектов используйте команду проекта и новый разговор.')
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
        if self._document().get('project_sessions'): raise ControlError('В режиме проектов используйте команду проекта и новый разговор.')
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
        if self._document().get('project_sessions'): raise ControlError('В режиме проектов используйте команду проекта и новый разговор.')
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

    def project_status(self, settings, state, doc):
        if not doc.get('project_sessions'): return {}
        counts = {}
        for route in doc['routes']:
            counts[route['project_id']] = sum(1 for c in settings['chats'] if
                c.get('project_session') and c.get('gizmo_id') == route['project_id'] and
                not any(c.get(k) for k in ('conversation_id','voice_session_id','parent_message_id')) and
                any(x['id']==c['id'] for x in state.get('settings',{}).get('chats',[])))
        return {'prepared':counts,'hot_preparation':'prepare_chats_v1' in state.get('features',[]),'archive':self.archive_status()}

    def archive_status(self):
        from pathlib import Path
        import os
        from ordinary_archive import projects
        archive = {'available':False,'ordinary_projects':projects()}
        try:
            a=json.loads((Path(os.environ['LOCALAPPDATA'])/'KasControl/Archive/status.json').read_text(encoding='utf-8'))
            archive={'available':not (Path(os.environ['LOCALAPPDATA'])/'KasControl/Archive/stop').exists() and time.time()-max(a.get('completed_at',0),a.get('heartbeat_at',0))<180,
                     'remaining':a.get('remaining_sources',0), 'updated':a.get('completed_at') or a.get('heartbeat_at'),'note':a.get('note'),
                     'empty':a.get('empty_sources',0),'missing':a.get('missing_sources',0),
                     'blocked':a.get('blocked_sources',0),'voice_copied':a.get('voice_copied',0),'items':a.get('queue',[])}
            archive.update(ordinary_projects=projects(),ordinary=a.get('ordinary',{}))
        except (OSError,ValueError,KeyError): pass
        return archive

    def set_ordinary_archive(self,project,mode):
        from ordinary_archive import set_project
        self._project(project)
        return {'ordinary_projects':set_project(project,mode)}

    def save_project_command(self, project, phrase):
        self._ready(); name=self._project(project); doc=self._document()
        if not doc.get('project_sessions'): raise ControlError('Режим команд проектов не включен.')
        phrase=normalize_phrase(phrase)
        reserved={'открой чат','закрой чат','заверши чат','хватит'}
        reserved.update(normalize_phrase(t) for a,t in self.store.native_commands() if a!=1)
        if phrase in reserved: raise ControlError('Эта фраза занята управлением разговором.')
        if any(r['phrase']==phrase and r['project_id']!=project for r in doc['routes']):
            raise ControlError('Эта фраза уже назначена другому проекту.')
        route=next((r for r in doc['routes'] if r['project_id']==project),None)
        if route: route['phrase']=phrase
        else:
            settings=self.station.read_settings()
            unmanaged=sum(not c.get('project_session') for c in settings['chats'])
            if unmanaged+(len(doc['routes'])+1)*8+2>64: raise ControlError('Недостаточно места для запаса еще одного проекта.')
            ident=str(uuid.uuid4())
            doc['catalog'].append({'chat_id':ident,'project_id':project,'title':name,'project_name':name,
                                   'conversation_id':'','parent_message_id':'','project_session':True})
            doc['routes'].append({'phrase':phrase,'chat_id':ident,'project_id':project,'conversation_id':''})
        self.store.write(doc)
        return {'phrase':phrase}

    def remove_project_command(self, project):
        self._ready(); self._project(project); doc=self._document()
        if not doc.get('project_sessions'): raise ControlError('Режим команд проектов не включен.')
        routes=[r for r in doc['routes'] if r['project_id']!=project]
        if not routes: raise ControlError('Оставьте хотя бы один голосовой проект.')
        doc['routes']=routes
        doc['catalog']=[c for c in doc['catalog'] if c['project_id']!=project]
        self.store.write(doc)
        return {}

    def open_project(self, project):
        self._ready(); validate_id(project); doc=self._document()
        if not doc.get('project_sessions'): raise ControlError('Режим команд проектов не включен.')
        route=next((r for r in doc['routes'] if r['project_id']==project),None)
        if not route: raise ControlError('Сначала назначьте команду проекту.')
        result=json.loads(self.station.run(ROUTER+'/router open '+validate_id(route['chat_id'])+' || true',timeout=60))
        if result.get('ok') is not True: raise ControlError('Открытие не подтверждено. Обновите состояние.')
        return result

    def reconnect_project(self,project):
        snapshot=self.station.management_snapshot();settings=snapshot['settings'];state=snapshot['state']
        if not snapshot['routes'].get('project_sessions'):raise ControlError('Восстановление доступно в режиме проектов.')
        validate_id(project)
        old=next((c for c in settings['chats'] if c['id']==settings.get('selected')),None)
        if state.get('phase')!='active' or not old or old.get('gizmo_id')!=project:
            raise ControlError('В выбранном проекте нет активного разговора. Откройте новый разговор.')
        self.open_project(project)  # Existing router guards teardown, registration and rollback.
        after=self.station.management_snapshot();current=after['settings']
        fresh=next((c for c in current['chats'] if c['id']==current.get('selected')), {})
        prior=next((c for c in current['chats'] if c['id']==old['id']),{})
        if (after['state'].get('pid')!=state.get('pid') or current.get('voice')!=settings.get('voice')
                or after['state'].get('phase')!='active' or current.get('selected')==old['id']
                or fresh.get('gizmo_id')!=project or (old.get('conversation_id') and fresh.get('conversation_id')==old['conversation_id'])
                or prior.get('conversation_id')!=old.get('conversation_id')):
            raise ControlError('Переподключение не подтверждено. Обновите состояние; повтор автоматически не выполняется.')
        return {'reconnected':True,'previous_preserved':True}

    def diagnostics(self):
        from diagnostics import save
        from archive_control import ArchiveControl
        from archive_schedule import enabled,timer_status
        archive=self.archive_status();archive.update(running=ArchiveControl().working,hourly=enabled(),**timer_status())
        return save(self.station.management_snapshot(),archive)

    def open_diagnostics_folder(self):
        import os
        from pathlib import Path
        root=Path(os.environ['LOCALAPPDATA'])/'KasControl/Diagnostics'
        if not root.is_dir():raise ControlError('Сначала соберите диагностику.')
        os.startfile(str(root))
        return {}
