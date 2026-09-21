"""Phase 3 tests: the read-only analytics endpoints (API_CONTRACT.md §6)."""
from decimal import Decimal

from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from apps.contributions.models import Contribution
from apps.payments.models import Payment
from apps.users.models import Department, User


class AnalyticsContractTests(APITestCase):
    def setUp(self):
        self.department = Department.objects.create(
            name='Computer Science', faculty='Physical Sciences'
        )
        self.other_department = Department.objects.create(
            name='Physics', faculty='Physical Sciences'
        )
        self.rep = User.objects.create_user(
            username='rep1', email='rep1@example.com',
            password='S7rong!Passw0rd', matric_number='CSC/2021/003',
            department=self.department, level='500',
            role=User.ROLE_CLASS_REP,
        )
        self.student1 = User.objects.create_user(
            username='student1', email='student1@example.com',
            password='S7rong!Passw0rd', matric_number='CSC/2021/041',
            department=self.department, level='400',
        )
        self.student2 = User.objects.create_user(
            username='student2', email='student2@example.com',
            password='S7rong!Passw0rd', matric_number='CSC/2021/042',
            department=self.department, level='500',
        )
        self.outsider_rep = User.objects.create_user(
            username='rep2', email='rep2@example.com',
            password='S7rong!Passw0rd', matric_number='PHY/2021/003',
            department=self.other_department, level='500',
            role=User.ROLE_CLASS_REP,
        )
        self.fee = Contribution.objects.create(
            department=self.department, created_by=self.rep,
            title='Excursion Fee', amount=Decimal('5000.00'),
        )
        self.level_fee = Contribution.objects.create(
            department=self.department, created_by=self.rep,
            title='400-Level Dues', amount=Decimal('1000.00'),
            target_level='400',
        )
        self.other_fee = Contribution.objects.create(
            department=self.other_department, created_by=self.outsider_rep,
            title='Physics Dues', amount=Decimal('2000.00'),
        )

    def _auth(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def _mark_paid(self, user, contribution):
        Payment.objects.create(
            student=user,
            contribution=contribution,
            payment_type=Payment.PAYMENT_CONTRIBUTION,
            amount=contribution.amount,
            reference=f'MANUAL-{contribution.id}-{user.id}',
            status=Payment.STATUS_SUCCESS,
            method=Payment.METHOD_MANUAL,
            recorded_by=self.rep,
            receipt_reference='TELLER-001',
        )

    # --- permissions -------------------------------------------------------

    def test_students_are_forbidden_on_every_analytics_endpoint(self):
        self._auth(self.student1)

        for url in (
            '/api/analytics/collection-stats/',
            f'/api/analytics/outstanding-students/?contribution_id={self.fee.id}',
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(response.data['error'], 'permission_denied')

    def test_unauthenticated_requests_get_the_standard_401(self):
        response = self.client.get('/api/analytics/collection-stats/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data['error'], 'unauthorized')

    # --- collection-stats ---------------------------------------------------

    def test_collection_stats_scoped_to_one_fee(self):
        # fee is open to rep + both students: 3 × 5000 = 15000 expected.
        self._mark_paid(self.student1, self.fee)
        self._auth(self.rep)

        response = self.client.get(
            f'/api/analytics/collection-stats/?contribution_id={self.fee.id}'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                'total_expected': '15000.00',
                'total_collected': '5000.00',
                'outstanding_count': 2,
            },
        )

    def test_collection_stats_without_id_covers_the_whole_department(self):
        # fee: 3 × 5000; level_fee: 1 × 1000 (400-level only) = 16000 expected.
        self._mark_paid(self.student1, self.fee)
        self._auth(self.rep)

        response = self.client.get('/api/analytics/collection-stats/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                'total_expected': '16000.00',
                'total_collected': '5000.00',
                'outstanding_count': 3,
            },
        )

    def test_collection_stats_never_leaks_another_department(self):
        self._auth(self.rep)

        response = self.client.get(
            f'/api/analytics/collection-stats/?contribution_id={self.other_fee.id}'
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data['error'], 'not_found')

    def test_stats_match_the_per_fee_summary_endpoint(self):
        """§6 promises one set of numbers — analytics can never disagree
        with /contributions/{id}/summary/."""
        self._mark_paid(self.student1, self.fee)
        self._auth(self.rep)

        summary = self.client.get(f'/api/contributions/{self.fee.id}/summary/')
        stats = self.client.get(
            f'/api/analytics/collection-stats/?contribution_id={self.fee.id}'
        )

        self.assertEqual(summary.status_code, status.HTTP_200_OK)
        self.assertEqual(stats.status_code, status.HTTP_200_OK)
        self.assertEqual(stats.data['total_expected'], summary.data['total_expected'])
        self.assertEqual(stats.data['total_collected'], summary.data['total_collected'])
        self.assertEqual(
            stats.data['outstanding_count'], summary.data['outstanding_count']
        )

    # --- outstanding-students -----------------------------------------------

    def test_outstanding_students_requires_a_contribution_id(self):
        self._auth(self.rep)

        response = self.client.get('/api/analytics/outstanding-students/')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error'], 'bad_request')

    def test_outstanding_students_rejects_a_non_numeric_id(self):
        self._auth(self.rep)

        response = self.client.get(
            '/api/analytics/outstanding-students/?contribution_id=abc'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error'], 'bad_request')

    def test_outstanding_students_never_leaks_another_department(self):
        self._auth(self.rep)

        response = self.client.get(
            f'/api/analytics/outstanding-students/?contribution_id={self.other_fee.id}'
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data['error'], 'not_found')

    def test_outstanding_students_lists_only_unpaid_with_safe_fields(self):
        self._mark_paid(self.student1, self.fee)
        self._auth(self.rep)

        response = self.client.get(
            f'/api/analytics/outstanding-students/?contribution_id={self.fee.id}'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        matrics = [row['matric_number'] for row in response.data]
        # The paid student is excluded; rep + student2 remain, matric order.
        self.assertEqual(matrics, ['CSC/2021/003', 'CSC/2021/042'])
        self.assertEqual(
            set(response.data[0].keys()),
            {'id', 'full_name', 'matric_number', 'level'},
        )