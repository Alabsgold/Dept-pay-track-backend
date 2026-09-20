"""Agent 5 (QA) — contract-shape tests for Auth & Departments endpoints.

Asserts API_CONTRACT.md §1/§2 documented response shapes and §7 error shape.
Where the contract is silent, locks the current shape for stability.
"""
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase

from apps.users.models import Department, User


class AuthContractTests(APITestCase):
    def setUp(self):
        # Throttle history lives in the shared LocMem cache and persists
        # across tests within a run — clear it so every test starts with a
        # fresh 10/min budget (same pattern as test_login_is_rate_limited).
        cache.clear()
        self.department = Department.objects.create(
            name='Computer Science', faculty='Physical Sciences'
        )
        self.user = User.objects.create_user(
            username='jdoe', email='jdoe@school.edu.ng',
            password='S7rong!Passw0rd', matric_number='CSC/2021/045',
            department=self.department, level='400',
        )

    def _register(self, **overrides):
        payload = {
            'username': 'newstudent', 'email': 'new@school.edu.ng',
            'password': 'S7rong!Passw0rd', 'matric_number': 'CSC/2021/099',
            'department_id': self.department.id, 'level': '400',
        }
        payload.update(overrides)
        return self.client.post('/api/auth/register/', payload, format='json')

    def _token_auth(self):
        from rest_framework.authtoken.models import Token
        token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def test_register_returns_documented_201_shape(self):
        response = self._register()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # Contract §1: {id, username, role, department, level}
        self.assertEqual(
            set(response.data.keys()),
            {'id', 'username', 'role', 'department', 'level'},
        )
        self.assertEqual(response.data['role'], 'student')
        self.assertEqual(response.data['department'], 'Computer Science')
        self.assertEqual(response.data['level'], '400')

    def test_duplicate_registration_case_insensitive_is_generic(self):
        # §8: any duplicate identifier gets ONE generic, field-anonymous
        # message (raised non-field; the handler no longer prefixes it).
        response = self._register(
            username='other', email='other@school.edu.ng',
            matric_number='csc/2021/045',  # case-insensitive duplicate
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data['message'],
            'Unable to register with the provided details.',
        )

    def test_duplicate_registration_exact_case_is_generic(self):
        # QA M-5 FIXED: an exact-case duplicate used to leak DRF's
        # UniqueValidator message ("User with this matric number already
        # exists."); the object-level check now returns the same generic,
        # field-anonymous message for every identifier.
        response = self._register(
            username='other', email='other@school.edu.ng',
            matric_number='CSC/2021/045',  # exact-case duplicate
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data['message'],
            'Unable to register with the provided details.',
        )
        self.assertNotIn('matric', response.data['message'].lower())

    def test_duplicate_registration_username_is_generic(self):
        # QA M-5 (extension): username duplicates are covered too.
        response = self._register(
            username='jdoe',  # duplicate of the setUp user
            email='fresh@school.edu.ng',
            matric_number='CSC/2021/777',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data['message'],
            'Unable to register with the provided details.',
        )
        self.assertNotIn('username', response.data['message'].lower())

    def test_login_returns_documented_shape(self):
        response = self.client.post(
            '/api/auth/login/',
            {'username': 'jdoe', 'password': 'S7rong!Passw0rd'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Contract §1: {token, user: {id, username, role}}
        self.assertEqual(set(response.data.keys()), {'token', 'user'})
        self.assertEqual(
            set(response.data['user'].keys()), {'id', 'username', 'role'}
        )
        self.assertEqual(response.data['user']['role'], 'student')

    def test_logout_invalidates_token(self):
        self._token_auth()
        response = self.client.post('/api/auth/logout/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        me_after = self.client.get('/api/auth/me/')
        self.assertEqual(me_after.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_get_returns_documented_shape(self):
        self._token_auth()
        response = self.client.get('/api/auth/me/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data.keys()),
            {'id', 'username', 'email', 'matric_number', 'department',
             'level', 'role', 'phone_number', 'full_name'},
        )
        self.assertEqual(response.data['department'], 'Computer Science')

    def test_me_patch_updates_editable_fields_only(self):
        self._token_auth()
        response = self.client.patch(
            '/api/auth/me/',
            {'phone_number': '08012345678', 'level': '500',
             'matric_number': 'HACK/999/999'},  # must be ignored (read-only)
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['phone_number'], '08012345678')
        self.assertEqual(response.data['level'], '500')
        self.assertEqual(response.data['matric_number'], 'CSC/2021/045')

    def test_departments_list_shape(self):
        response = self.client.get('/api/departments/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data), 1)
        self.assertEqual(
            set(response.data[0].keys()), {'id', 'name', 'faculty'}
        )

    def test_error_shape_is_standard(self):
        # §7: every error is {error, message}.
        response = self.client.get('/api/auth/me/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(set(response.data.keys()), {'error', 'message'})
        self.assertEqual(response.data['error'], 'unauthorized')

    def test_login_throttled_at_429_with_standard_shape(self):
        # §8: login/register rate-limited at 10/min per IP (settings).
        # NOTE: ScopedRateThrottle reads its rate from a class attribute
        # evaluated at import time, so override_settings cannot retune it —
        # test against the real 10/min rate instead.
        cache.clear()  # isolate from other tests' throttle history
        for _ in range(10):
            self.client.post(
                '/api/auth/login/',
                {'username': 'jdoe', 'password': 'wrong'},
                format='json',
            )
        response = self.client.post(
            '/api/auth/login/',
            {'username': 'jdoe', 'password': 'wrong'},
            format='json',
        )
        self.assertEqual(
            response.status_code, status.HTTP_429_TOO_MANY_REQUESTS
        )
        self.assertEqual(set(response.data.keys()), {'error', 'message'})
        cache.clear()  # restore a clean cache for subsequent tests
