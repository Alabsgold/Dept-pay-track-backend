"""Phase 1 contract tests for notification serializer hardening."""
from decimal import Decimal

from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from apps.contributions.models import Contribution
from apps.users.models import Department, User

from .models import Notification


class NotificationSafeFieldsContractTests(APITestCase):
    def setUp(self):
        self.department = Department.objects.create(
            name='Computer Science', faculty='Physical Sciences'
        )
        self.student = User.objects.create_user(
            username='student1',
            email='student1@example.com',
            password='S7rong!Passw0rd',
            matric_number='CSC/2026/045',
            department=self.department,
            level='400',
        )
        self.contribution = Contribution.objects.create(
            department=self.department,
            created_by=self.student,
            title='Departmental Shirt 2026',
            amount=Decimal('3500.00'),
        )
        self.notification = Notification.objects.create(
            recipient=self.student,
            notification_type=Notification.TYPE_PAYMENT_SUCCESS,
            message='Your payment was successful.',
            contribution=self.contribution,
        )
        token, _ = Token.objects.get_or_create(user=self.student)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.safe_fields = {
            'id',
            'notification_type',
            'message',
            'contribution',
            'contribution_title',
            'is_read',
            'created_at',
        }

    def test_list_exposes_only_safe_fields(self):
        response = self.client.get('/api/notifications/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        alert = next(
            row for row in response.data
            if row['id'] == self.notification.id
        )
        self.assertEqual(set(alert.keys()), self.safe_fields)
        self.assertNotIn('recipient', alert)
        self.assertEqual(
            alert['contribution_title'],
            'Departmental Shirt 2026',
        )

    def test_mark_read_exposes_only_safe_fields(self):
        response = self.client.post(
            f'/api/notifications/{self.notification.id}/read/'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(response.data.keys()), self.safe_fields)
        self.assertNotIn('recipient', response.data)
        self.assertTrue(response.data['is_read'])
