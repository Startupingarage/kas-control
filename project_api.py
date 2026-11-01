"""Create an empty ChatGPT project using the station's existing web session."""
import unicodedata

from backend import ControlError, validate_id


def validate_name(name):
    if (not isinstance(name, str) or not name.strip() or len(name) > 100
            or any(unicodedata.category(c).startswith('C') for c in name)):
        raise ControlError('Название проекта: от 1 до 100 символов, без управляющих символов.')
    return name.strip()


def create_project(cloud, name):
    name = validate_name(name)
    # Current first-party frontend: 7078.6cca9159bc.js, QmkR/tl/tG.
    # shortcut: private web API, reverify the frontend protocol if OpenAI changes it.
    if cloud.http is None:
        cloud.projects()  # Initializes credentials in memory through the existing Cloud path.
    uncertain = ('Результат создания проекта не подтверждён. Обновите список проектов '
                 'перед повторной попыткой: проект мог быть создан.')
    try:
        response = cloud.http.post('https://chatgpt.com/backend-api/projects',
                                   json={'name': name, 'instructions': ''},
                                   timeout=25, allow_redirects=False)
    except Exception:
        raise ControlError(uncertain) from None
    if response.status_code not in (200, 201):
        # Never retry a mutation or expose response bodies containing account details.
        raise ControlError('ChatGPT вернул HTTP ' + str(response.status_code) + '. ' + uncertain)
    try:
        data = response.json()
        if data.get('error') is not None:
            raise ValueError('Project API error')
        project_id = validate_id(data['resource']['gizmo']['id'])
        if not project_id.startswith('g-p-'):
            raise ValueError('Not a project')
        saved = cloud.request('/gizmos/' + project_id)['gizmo']
        if (saved['id'] != project_id or saved['display']['name'] != name
                or saved.get('is_archived')):
            raise ValueError('Project readback mismatch')
    except Exception:
        raise ControlError(uncertain) from None
    return {'id': project_id, 'name': name}
