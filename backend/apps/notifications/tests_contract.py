"""Agent 5 (QA) — contract-shape tests for Notifications endpoints (§5).

The contract lists the two endpoints but no response shape (flagged in
BOTTLENECKS.md); these tests lock the shipped shape for stability.
"""
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from apps.users.models import Department, User

from .models import Notification


class NotificationContractTests(APITestCase):
    def setUp(self):
        self.department = Department.objects.create(
            name='Computer Science', faculty='Physical Sciences'
        )
        self.student = User.objects.create_user(
            username='student1', email='student1@example.com',
            password='S7rong!Passw0rd', matric_number='CSC/2021/045',
            department=self.department, level='400',
        )
        self.alert = Notification.objects.create(
            recipient=self.student,
            notification_type=Notification.TYPE_PAYMENT_SUCCESS,
            message='Your payment was successful.',
        )
        token, _ = Token.objects.get_or_create(user=self.student)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def test_list_locks_current_shape(self):
        response = self.client.get('/api/notifications/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            set(response.data[0].keys()),
            {'id', 'notification_type', 'message', 'contribution',
             'contribution_title', 'is_read', 'created_at'},
        )
        self.assertEqual(
            response.data[0]['notification_type'], 'payment_success'
        )
        self.assertFalse(response.data[0]['is_read'])

    def test_mark_read_returns_updated_object(self):
        response = self.client.post(
            f'/api/notifications/{self.alert.id}/read/'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['is_read'])
