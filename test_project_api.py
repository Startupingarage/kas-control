import unittest
from unittest.mock import Mock

from backend import ControlError
from project_api import create_project


class ProjectTests(unittest.TestCase):
    def cloud(self):
        cloud = Mock()
        cloud.http.post.return_value.status_code = 200
        cloud.http.post.return_value.json.return_value = {'resource': {'gizmo': {'id': 'g-p-test'}}}
        cloud.request.return_value = {'gizmo': {'id': 'g-p-test', 'display': {'name': 'Проект'},'memory_scope':'global'}}
        return cloud

    def test_create_and_readback(self):
        cloud = self.cloud()
        self.assertEqual(create_project(cloud, ' Проект '), {'id': 'g-p-test', 'name': 'Проект'})
        cloud.http.post.assert_called_once_with('https://chatgpt.com/backend-api/projects',
                                               json={'name': 'Проект', 'instructions': ''},
                                               timeout=25, allow_redirects=False)
        cloud.request.assert_called_once_with('/gizmos/g-p-test')

    def test_initialize_existing_session(self):
        cloud = self.cloud()
        session = cloud.http
        cloud.http = None
        cloud.projects.side_effect = lambda: setattr(cloud, 'http', session)
        create_project(cloud, 'Проект')
        cloud.projects.assert_called_once_with()

    def test_invalid_names_never_access_network(self):
        for name in (None, '', '   ', 'a' * 101, 'a\nb', '\x00', '\ud800', 'a\u202eb'):
            with self.subTest(name=repr(name)):
                cloud = self.cloud()
                with self.assertRaises(ControlError):
                    create_project(cloud, name)
                cloud.http.post.assert_not_called()
                cloud.projects.assert_not_called()

    def test_network_failure_has_no_retry_or_secret(self):
        cloud = self.cloud()
        cloud.http.post.side_effect = RuntimeError('SECRET')
        with self.assertRaises(ControlError) as caught:
            create_project(cloud, 'Проект')
        self.assertNotIn('SECRET', str(caught.exception))
        cloud.http.post.assert_called_once()

    def test_rejected_mutation_not_retried(self):
        for status in (302, 401, 403, 422, 429, 500):
            with self.subTest(status=status):
                cloud = self.cloud()
                cloud.http.post.return_value.status_code = status
                with self.assertRaises(ControlError):
                    create_project(cloud, 'Проект')
                cloud.http.post.assert_called_once()
                cloud.request.assert_not_called()

    def test_missing_invalid_or_wrong_returned_id(self):
        for data in ({}, {'resource': {'gizmo': {'id': 'g-other'}}},
                     {'resource': {'gizmo': {'id': '../bad'}}},
                     {'error': {'message': 'SECRET'}, 'resource': {'gizmo': {'id': 'g-p-test'}}}):
            cloud = self.cloud()
            cloud.http.post.return_value.json.return_value = data
            with self.assertRaises(ControlError):
                create_project(cloud, 'Проект')
            cloud.http.post.assert_called_once()

    def test_readback_failure_is_uncertain_no_retry(self):
        for saved in ({}, {'gizmo': {'id': 'g-p-other', 'display': {'name': 'Проект'}}},
                      {'gizmo': {'id': 'g-p-test', 'display': {'name': 'Other'}}},
                      {'gizmo': {'id': 'g-p-test', 'display': {'name': 'Проект'}, 'is_archived': True}}):
            cloud = self.cloud()
            cloud.request.return_value = saved
            with self.assertRaisesRegex(ControlError, 'мог быть создан'):
                create_project(cloud, 'Проект')
            cloud.http.post.assert_called_once()


if __name__ == '__main__':
    unittest.main()
