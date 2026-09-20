from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from apps.users.models import Department, User


class LoginIdentifierContractTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.department = Department.objects.create(
            name='Computer Science',
            faculty='Physical Sciences',
        )
        self.user = User.objects.create_user(
            username='student.one',
            email='student@example.com',
            password='S7rong!Passw0rd',
            matric_number='CSC/2026/001',
            department=self.department,
            level='400',
        )
        self.login_url = reverse('auth-login')

    def test_login_accepts_matric_number_case_insensitively(self):
        response = self.client.post(
            self.login_url,
            {'matric_number': 'csc/2026/001', 'password': 'S7rong!Passw0rd'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['user']['id'], self.user.id)
        self.assertTrue(
            Token.objects.filter(user=self.user, key=response.data['token']).exists()
        )

    def test_login_accepts_email_case_insensitively(self):
        response = self.client.post(
            self.login_url,
            {'email': 'STUDENT@EXAMPLE.COM', 'password': 'S7rong!Passw0rd'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['user']['id'], self.user.id)

    def test_login_falls_back_to_username_exactly_as_submitted(self):
        wrong_case = self.client.post(
            self.login_url,
            {'username': 'STUDENT.ONE', 'password': 'S7rong!Passw0rd'},
            format='json',
        )
        exact_case = self.client.post(
            self.login_url,
            {'username': 'student.one', 'password': 'S7rong!Passw0rd'},
            format='json',
        )

        self.assertEqual(wrong_case.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            wrong_case.data,
            {'error': 'bad_request', 'message': 'Invalid username or password.'},
        )
        self.assertEqual(exact_case.status_code, status.HTTP_200_OK)
        self.assertEqual(exact_case.data['user']['id'], self.user.id)

    def test_login_failures_are_indistinguishable(self):
        unclaimed = User.objects.create_user(
            username='unclaimed.student',
            email='unclaimed@example.com',
            password='S7rong!Passw0rd',
            matric_number='CSC/2026/002',
            department=self.department,
            level='400',
        )
        unclaimed.set_unusable_password()
        unclaimed.save(update_fields=['password'])
        inactive = User.objects.create_user(
            username='inactive.student',
            email='inactive@example.com',
            password='S7rong!Passw0rd',
            matric_number='CSC/2026/003',
            department=self.department,
            level='400',
            is_active=False,
        )

        expected = {
            'error': 'bad_request',
            'message': 'Invalid username or password.',
        }
        attempts = (
            {'username': 'nobody@example.com', 'password': 'S7rong!Passw0rd'},
            {'username': self.user.username, 'password': 'wrong-password'},
            {'username': unclaimed.username, 'password': 'S7rong!Passw0rd'},
            {'username': inactive.username, 'password': 'S7rong!Passw0rd'},
        )
        responses = [
            self.client.post(self.login_url, payload, format='json')
            for payload in attempts
        ]

        for response in responses:
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(response.data, expected)

    def test_login_throttles_alternate_identifier_attempts(self):
        for _ in range(10):
            response = self.client.post(
                self.login_url,
                {'email': 'student@example.com', 'password': 'S7rong!Passw0rd'},
                format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.post(
            self.login_url,
            {'email': 'student@example.com', 'password': 'S7rong!Passw0rd'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class RegistrationHardeningContractTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.department = Department.objects.create(
            name='Computer Science',
            faculty='Physical Sciences',
        )
        self.register_url = reverse('auth-register')

    def _registration_payload(self, **overrides):
        payload = {
            'username': 'new.student',
            'email': 'new.student@example.com',
            'password': 'S7rong!Passw0rd',
            'matric_number': 'CSC/2026/100',
            'department_id': self.department.id,
            'level': '400',
        }
        payload.update(overrides)
        return payload

    def test_registration_rejects_username_containing_at_sign(self):
        response = self.client.post(
            self.register_url,
            self._registration_payload(username='student@example.com'),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            User.objects.filter(username='student@example.com').exists()
        )

    def test_registration_duplicate_email_is_case_insensitive_and_generic(self):
        User.objects.create_user(
            username='existing.student',
            email='student@example.com',
            password='S7rong!Passw0rd',
            matric_number='CSC/2026/099',
            department=self.department,
            level='400',
        )
        response = self.client.post(
            self.register_url,
            self._registration_payload(
                username='other.student',
                email='STUDENT@EXAMPLE.COM',
                matric_number='CSC/2026/101',
            ),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data,
            {
                'error': 'bad_request',
                'message': 'Unable to register with the provided details.',
            },
        )

    def test_registration_duplicate_username_is_case_insensitive_and_generic(self):
        User.objects.create_user(
            username='new.student',
            email='existing.student@example.com',
            password='S7rong!Passw0rd',
            matric_number='CSC/2026/098',
            department=self.department,
            level='400',
        )
        response = self.client.post(
            self.register_url,
            self._registration_payload(
                username='NEW.STUDENT',
                email='other.student@example.com',
                matric_number='CSC/2026/101',
            ),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data,
            {
                'error': 'bad_request',
                'message': 'Unable to register with the provided details.',
            },
        )

    def test_registration_cannot_set_privileged_account_fields(self):
        response = self.client.post(
            self.register_url,
            self._registration_payload(
                role=User.ROLE_ADMIN,
                is_staff=True,
                is_superuser=True,
                is_active=False,
            ),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = User.objects.get(username='new.student')
        self.assertEqual(created.role, User.ROLE_STUDENT)
        self.assertFalse(created.is_staff)
        self.assertFalse(created.is_superuser)
        self.assertTrue(created.is_active)

    def test_profile_patch_cannot_set_privileged_account_fields(self):
        user = User.objects.create_user(
            username='profile.student',
            email='profile.student@example.com',
            password='S7rong!Passw0rd',
            matric_number='CSC/2026/102',
            department=self.department,
            level='400',
        )
        token = Token.objects.create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

        response = self.client.patch(
            reverse('auth-me'),
            {
                'phone_number': '08012345678',
                'role': User.ROLE_ADMIN,
                'is_staff': True,
                'is_superuser': True,
                'is_active': False,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertEqual(user.role, User.ROLE_STUDENT)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.is_active)
        self.assertEqual(user.phone_number, '08012345678')


class ProfileFullNameContractTests(APITestCase):
    def setUp(self):
        self.department = Department.objects.create(
            name='Computer Science',
            faculty='Physical Sciences',
        )
        self.user = User.objects.create_user(
            username='profile.student',
            email='profile.student@example.com',
            password='S7rong!Passw0rd',
            matric_number='CSC/2026/102',
            department=self.department,
            level='400',
            first_name='  Ada ',
            last_name=' Lovelace ',
        )
        token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

    def test_me_get_includes_trimmed_full_name(self):
        response = self.client.get(reverse('auth-me'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['full_name'], 'Ada Lovelace')

    def test_me_get_falls_back_to_username_when_names_are_blank(self):
        self.user.first_name = '  '
        self.user.last_name = ' '
        self.user.save(update_fields=['first_name', 'last_name'])

        response = self.client.get(reverse('auth-me'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['full_name'], 'profile.student')

    def test_me_patch_cannot_change_full_name(self):
        response = self.client.patch(
            reverse('auth-me'),
            {'full_name': 'Attacker Name', 'phone_number': '08012345678'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['full_name'], 'Ada Lovelace')
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, '  Ada ')
        self.assertEqual(self.user.last_name, ' Lovelace ')

