"""Agent 5 (QA) — contract-shape tests for Contributions endpoints (§3)."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from apps.contributions.models import Contribution
from apps.users.models import Department, User


class ContributionContractTests(APITestCase):
    def setUp(self):
        self.department = Department.objects.create(
            name='Computer Science', faculty='Physical Sciences'
        )
        self.student = User.objects.create_user(
            username='student1', email='student1@example.com',
            password='S7rong!Passw0rd', matric_number='CSC/2021/045',
            department=self.department, level='400',
        )
        self.rep = User.objects.create_user(
            username='rep1', email='rep@example.com',
            password='S7rong!Passw0rd', matric_number='CSC/2021/003',
            department=self.department, level='500',
            role=User.ROLE_CLASS_REP,
        )
        self.contribution = Contribution.objects.create(
            department=self.department, created_by=self.rep,
            title='Departmental Shirt 2026', description='desc',
            amount=Decimal('3500.00'),
            deadline=timezone.now() + timedelta(days=30),
            is_mandatory=True, target_level=None,
        )
        self._auth(self.student)

    def _auth(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def test_list_returns_documented_item_shape(self):
        response = self.client.get('/api/contributions/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        item = next(
            c for c in response.data if c['id'] == self.contribution.id
        )
        # Contract §3: {id, title, amount, deadline, is_mandatory,
        #               target_level, has_paid}
        self.assertEqual(
            set(item.keys()),
            {'id', 'title', 'amount', 'deadline', 'is_mandatory',
             'target_level', 'has_paid'},
        )
        # Money rule: amounts are strings, never floats.
        self.assertEqual(item['amount'], '3500.00')
        self.assertIsInstance(item['amount'], str)

    def test_create_by_rep_returns_201_same_shape(self):
        self._auth(self.rep)
        response = self.client.post(
            '/api/contributions/',
            {'title': 'Excursion Fee', 'description': 'Trip',
             'amount': '5000.00', 'deadline': '2026-10-15T23:59:00Z',
             'is_mandatory': True, 'target_level': '400'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            set(response.data.keys()),
            {'id', 'title', 'amount', 'deadline', 'is_mandatory',
             'target_level', 'has_paid', 'department_id'},
        )
        self.assertEqual(response.data['amount'], '5000.00')

    def test_student_create_is_403_with_standard_error_shape(self):
        # §3 + §7: students cannot create; 403 uses {error, message}.
        response = self.client.post(
            '/api/contributions/',
            {'title': 'Nope', 'amount': '100.00',
             'deadline': '2026-10-15T23:59:00Z'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(set(response.data.keys()), {'error', 'message'})

    def test_detail_returns_list_shape(self):
        response = self.client.get(f'/api/contributions/{self.contribution.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['title'], 'Departmental Shirt 2026')

    def test_summary_returns_expected_vs_collected(self):
        response = self.client.get(
            f'/api/contributions/{self.contribution.id}/summary/'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data.keys()),
            {'total_expected', 'total_collected', 'outstanding_count'},
        )
        self.assertIsInstance(response.data['total_expected'], str)
        # eligible_students() = department users with student/reps roles
        # (no target_level): the student AND the rep → 2 outstanding, 0 paid.
        self.assertEqual(response.data['outstanding_count'], 2)

    def test_roster_returns_documented_shape(self):
        # §3: GET /contributions/{id}/payments/ — rep/admin only.
        self._auth(self.rep)
        response = self.client.get(
            f'/api/contributions/{self.contribution.id}/payments/'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data), 1)
        self.assertEqual(
            set(response.data[0].keys()),
            {'student', 'matric_number', 'status', 'paid_at'},
        )
        self.assertEqual(response.data[0]['status'], 'pending')
        self.assertIsNone(response.data[0]['paid_at'])

    def test_roster_forbidden_for_students(self):
        response = self.client.get(
            f'/api/contributions/{self.contribution.id}/payments/'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_manual_mark_returns_documented_201_shape(self):
        # §3: POST /contributions/{id}/payments/ — manual offline payment.
        self._auth(self.rep)
        response = self.client.post(
            f'/api/contributions/{self.contribution.id}/payments/',
            {'matric_number': 'CSC/2021/045', 'receipt_reference': 'RCPT-C-001'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            set(response.data.keys()),
            {'student', 'matric_number', 'status', 'paid_at', 'method'},
        )
        self.assertEqual(response.data['status'], 'success')
        self.assertEqual(response.data['method'], 'manual')

    def test_manual_mark_duplicate_returns_409_already_paid(self):
        self._auth(self.rep)
        first = self.client.post(
            f'/api/contributions/{self.contribution.id}/payments/',
            {'matric_number': 'CSC/2021/045', 'receipt_reference': 'RCPT-C-002'},
            format='json',
        )
        second = self.client.post(
            f'/api/contributions/{self.contribution.id}/payments/',
            {'matric_number': 'CSC/2021/045', 'receipt_reference': 'RCPT-C-003'},
            format='json',
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(second.data['error'], 'already_paid')
