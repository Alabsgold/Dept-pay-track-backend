"""Phase 2 frontend integration tests for the authentication lifecycle."""
from django.core.cache import cache
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from apps.users.models import Department, User


class AuthLifecycleIntegrationTests(APITestCase):
    """Exercise the exact v2.1 sequence used by a browser session."""

    def setUp(self):
        cache.clear()
        self.department = Department.objects.create(
            name='Computer Science', faculty='Physical Sciences'
        )

    def tearDown(self):
        cache.clear()

    def _register_payload(self):
        return {
            'username': 'frontend.student',
            'email': 'FRONTEND.STUDENT@EXAMPLE.COM',
            'password': 'S7rong!Passw0rd',
            'matric_number': 'CSC/2026/045',
            'department_id': self.department.id,
            'level': '400',
            # Unknown/unprivileged fields must be ignored.
            'full_name': 'Attacker Name',
            'role': User.ROLE_ADMIN,
            'is_staff': True,
            'is_superuser': True,
            'is_active': False,
        }

    def test_frontend_auth_lifecycle(self):
        register_response = self.client.post(
            '/api/auth/register/',
            self._register_payload(),
            format='json',
        )
        self.assertEqual(register_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            set(register_response.data.keys()),
            {'id', 'username', 'role', 'department', 'level'},
        )
        self.assertEqual(register_response.data['username'], 'frontend.student')
        self.assertEqual(register_response.data['role'], User.ROLE_STUDENT)

        created = User.objects.get(username='frontend.student')
        self.assertTrue(created.is_active)
        self.assertFalse(created.is_staff)
        self.assertFalse(created.is_superuser)

        login_response = self.client.post(
            '/api/auth/login/',
            {
                'email': 'frontend.student@example.com',
                'password': 'S7rong!Passw0rd',
            },
            format='json',
        )
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(login_response.data.keys()),
            {'token', 'user'},
        )
        self.assertEqual(
            set(login_response.data['user'].keys()),
            {'id', 'username', 'role'},
        )
        self.assertEqual(login_response.data['user']['id'], created.id)
        token = login_response.data['token']
        self.assertTrue(Token.objects.filter(user=created, key=token).exists())

        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token)
        me_response = self.client.get('/api/auth/me/')
        self.assertEqual(me_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(me_response.data.keys()),
            {
                'id', 'username', 'email', 'matric_number', 'department',
                'full_name', 'level', 'role', 'phone_number',
            },
        )
        self.assertEqual(me_response.data['email'], 'FRONTEND.STUDENT@example.com')
        self.assertEqual(me_response.data['full_name'], 'frontend.student')

        profile_response = self.client.patch(
            '/api/auth/me/',
            {
                'phone_number': '08012345678',
                'level': '500',
                'full_name': 'Attacker Name',
                'role': User.ROLE_ADMIN,
                'is_staff': True,
                'is_superuser': True,
                'is_active': False,
                'email': 'attacker@example.com',
                'matric_number': 'HACK/2026/999',
            },
            format='json',
        )
        self.assertEqual(profile_response.status_code, status.HTTP_200_OK)
        self.assertEqual(profile_response.data['phone_number'], '08012345678')
        self.assertEqual(profile_response.data['level'], '500')
        self.assertEqual(profile_response.data['full_name'], 'frontend.student')
        self.assertEqual(profile_response.data['role'], User.ROLE_STUDENT)
        self.assertEqual(
            profile_response.data['email'], 'FRONTEND.STUDENT@example.com'
        )
        self.assertEqual(
            profile_response.data['matric_number'], 'CSC/2026/045'
        )

        created.refresh_from_db()
        self.assertEqual(created.role, User.ROLE_STUDENT)
        self.assertFalse(created.is_staff)
        self.assertFalse(created.is_superuser)
        self.assertTrue(created.is_active)

        logout_response = self.client.post('/api/auth/logout/')
        self.assertEqual(logout_response.status_code, status.HTTP_200_OK)
        self.assertFalse(Token.objects.filter(key=token).exists())

        protected_response = self.client.get('/api/auth/me/')
        self.assertEqual(protected_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(
            set(protected_response.data.keys()),
            {'error', 'message'},
        )
        self.assertEqual(protected_response.data['error'], 'unauthorized')
