import hashlib
import hmac
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from .models import Payment


User = get_user_model()


class PaymentTests(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='student1',
            email='student1@example.com',
            password='TestPassword123!',
            matric_number='TEST/2026/001',
        )

        self.other_user = User.objects.create_user(
            username='student2',
            email='student2@example.com',
            password='TestPassword123!',
            matric_number='TEST/2026/002',
        )

        self.admin = User.objects.create_user(
            username='admin1',
            email='admin@example.com',
            password='TestPassword123!',
            matric_number='TEST/2026/003',
            role='admin',
        )

        self.token = Token.objects.create(user=self.user)
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Token {self.token.key}'
        )

    def test_unauthenticated_user_cannot_view_payments(self):
        self.client.credentials()

        response = self.client.get('/api/payments/')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_student_can_only_view_their_own_payments(self):
        Payment.objects.create(
            student=self.user,
            payment_type=Payment.PAYMENT_DEPARTMENTAL_FEE,
            amount=5000,
            reference='REF-001',
        )

        Payment.objects.create(
            student=self.other_user,
            payment_type=Payment.PAYMENT_EXCURSION,
            amount=10000,
            reference='REF-002',
        )

        response = self.client.get('/api/payments/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['reference'], 'REF-001')

    def test_invalid_payment_type_is_rejected(self):
        response = self.client.post(
            '/api/payments/initialize/',
            {
                'payment_type': 'invalid_type',
                'amount': 5000,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data['error'],
            'Invalid payment type.'
        )

    def test_invalid_amount_is_rejected(self):
        response = self.client.post(
            '/api/payments/initialize/',
            {
                'payment_type': Payment.PAYMENT_DEPARTMENTAL_FEE,
                'amount': 0,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('apps.payments.views.requests.post')
    def test_student_can_initialize_payment(self, mock_post):
        mock_post.return_value.json.return_value = {
            'status': True,
            'data': {
                'reference': 'TEST-REFERENCE-001',
                'authorization_url': 'https://checkout.paystack.com/test',
            },
        }

        response = self.client.post(
            '/api/payments/initialize/',
            {
                'payment_type': Payment.PAYMENT_DEPARTMENTAL_FEE,
                'amount': 5000,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        payment = Payment.objects.get(
            reference='TEST-REFERENCE-001'
        )

        self.assertEqual(payment.student, self.user)
        self.assertEqual(payment.amount, 5000)
        self.assertEqual(
            payment.status,
            Payment.STATUS_PENDING
        )

    def test_student_cannot_access_admin_payments(self):
        response = self.client.get('/api/payments/admin/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_view_all_payments(self):
        Payment.objects.create(
            student=self.user,
            payment_type=Payment.PAYMENT_DEPARTMENTAL_FEE,
            amount=5000,
            reference='REF-ADMIN-001',
        )

        Payment.objects.create(
            student=self.other_user,
            payment_type=Payment.PAYMENT_EXCURSION,
            amount=10000,
            reference='REF-ADMIN-002',
        )

        admin_token = Token.objects.create(user=self.admin)

        self.client.credentials(
            HTTP_AUTHORIZATION=f'Token {admin_token.key}'
        )

        response = self.client.get('/api/payments/admin/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_webhook_rejects_missing_signature(self):
        response = self.client.post(
            '/api/payments/webhook/',
            {},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_webhook_rejects_invalid_signature(self):
        payload = json.dumps({
            'event': 'charge.success',
            'data': {
                'reference': 'REF-INVALID',
            },
        }).encode()

        response = self.client.post(
            '/api/payments/webhook/',
            payload,
            content_type='application/json',
            HTTP_X_PAYSTACK_SIGNATURE='invalid-signature',
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_webhook_marks_payment_as_success(self):
        payment = Payment.objects.create(
            student=self.user,
            payment_type=Payment.PAYMENT_DEPARTMENTAL_FEE,
            amount=5000,
            reference='REF-WEBHOOK-001',
            status=Payment.STATUS_PENDING,
        )

        payload = json.dumps({
            'event': 'charge.success',
            'data': {
                'reference': payment.reference,
            },
        }).encode()

        signature = hmac.new(
            self._get_secret_key(),
            payload,
            hashlib.sha512,
        ).hexdigest()

        response = self.client.post(
            '/api/payments/webhook/',
            payload,
            content_type='application/json',
            HTTP_X_PAYSTACK_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        payment.refresh_from_db()

        self.assertEqual(
            payment.status,
            Payment.STATUS_SUCCESS
        )

    def _get_secret_key(self):
        from django.conf import settings
        return settings.PAYSTACK_SECRET_KEY.encode()
