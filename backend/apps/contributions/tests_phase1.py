"""Phase 1 contract tests for contribution creation hardening."""
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from apps.contributions.models import Contribution
from apps.users.models import Department, User


class ContributionDeadlineContractTests(APITestCase):
    def setUp(self):
        self.department = Department.objects.create(
            name='Computer Science', faculty='Physical Sciences'
        )
        self.rep = User.objects.create_user(
            username='rep1',
            email='rep1@example.com',
            password='S7rong!Passw0rd',
            matric_number='CSC/2021/003',
            department=self.department,
            level='500',
            role=User.ROLE_CLASS_REP,
        )
        token, _ = Token.objects.get_or_create(user=self.rep)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def _payload(self, deadline='2026-10-15T23:59:00Z'):
        return {
            'title': 'Excursion Fee',
            'description': 'Trip',
            'amount': '5000.00',
            'deadline': deadline,
            'is_mandatory': True,
            'target_level': '400',
        }

    def test_create_rejects_missing_deadline(self):
        payload = self._payload()
        payload.pop('deadline')

        response = self.client.post(
            '/api/contributions/', payload, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error'], 'bad_request')

    def test_create_allows_explicit_null_deadline(self):
        response = self.client.post(
            '/api/contributions/', self._payload(deadline=None), format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        saved = Contribution.objects.get(pk=response.data['id'])
        self.assertIsNone(saved.deadline)


class ContributionDepartmentResponseContractTests(APITestCase):
    def setUp(self):
        self.department = Department.objects.create(
            name='Computer Science', faculty='Physical Sciences'
        )
        self.rep = User.objects.create_user(
            username='rep1',
            email='rep1@example.com',
            password='S7rong!Passw0rd',
            matric_number='CSC/2021/003',
            department=self.department,
            level='500',
            role=User.ROLE_CLASS_REP,
        )
        token, _ = Token.objects.get_or_create(user=self.rep)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def test_create_response_includes_server_selected_department_id(self):
        response = self.client.post(
            '/api/contributions/',
            {
                'title': 'Excursion Fee',
                'description': 'Trip',
                'amount': '5000.00',
                'deadline': '2026-10-15T23:59:00Z',
                'is_mandatory': True,
                'target_level': '400',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('department_id', response.data)
        self.assertEqual(response.data['department_id'], self.department.pk)
