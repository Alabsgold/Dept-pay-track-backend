"""Phase 1 contract tests for gateway response and log hardening."""
from decimal import Decimal
import hashlib
import hmac
import json
from unittest.mock import patch

import requests
from django.conf import settings
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from apps.contributions.models import Contribution
from apps.users.models import Department, User

from .models import Payment


class PaymentGatewayHardeningTests(APITestCase):
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
            deadline=None,
            is_mandatory=True,
            target_level=None,
        )
        token, _ = Token.objects.get_or_create(user=self.student)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)
        self.generic_error = {
            'error': 'gateway_unavailable',
            'message': 'Payment gateway is unavailable. Try again shortly.',
        }

    def _payment(self, reference):
        return Payment.objects.create(
            student=self.student,
            contribution=self.contribution,
            payment_type=Payment.PAYMENT_CONTRIBUTION,
            amount=Decimal('3500.00'),
            reference=reference,
            status=Payment.STATUS_PENDING,
            method=Payment.METHOD_ONLINE,
        )

    def test_initiate_unsuccessful_gateway_response_is_sanitized_502(self):
        raw_marker = 'initialize-secret-marker'
        with patch('apps.payments.views.requests.post') as mock_post:
            mock_post.return_value.json.return_value = {
                'status': False,
                'message': raw_marker,
                'data': {},
            }
            response = self.client.post(
                '/api/payments/initiate/',
                {'contribution_id': self.contribution.id},
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.data, self.generic_error)
        self.assertNotIn(raw_marker, str(response.data))

    def test_verify_unsuccessful_gateway_response_is_sanitized_502(self):
        payment = self._payment('REF-HARDEN-VERIFY')
        raw_marker = 'verify-secret-marker'
        with patch('apps.payments.views.requests.get') as mock_get:
            mock_get.return_value.json.return_value = {
                'status': False,
                'message': raw_marker,
                'data': {},
            }
            response = self.client.get(
                f'/api/payments/verify/{payment.reference}/'
            )

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.data, self.generic_error)
        self.assertNotIn(raw_marker, str(response.data))

    def test_initiate_request_failure_is_sanitized_502_and_logged(self):
        with self.assertLogs('apps.payments.views', level='WARNING') as logs:
            with patch('apps.payments.views.requests.post') as mock_post:
                mock_post.side_effect = requests.exceptions.RequestException(
                    'exception-secret-marker'
                )
                response = self.client.post(
                    '/api/payments/initiate/',
                    {'contribution_id': self.contribution.id},
                    format='json',
                )

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.data, self.generic_error)
        self.assertNotIn('exception-secret-marker', str(response.data))
        self.assertIn('paystack initialize unavailable', '\n'.join(logs.output))
        self.assertNotIn('exception-secret-marker', '\n'.join(logs.output))

    def test_verify_request_failure_is_sanitized_502_and_logged(self):
        payment = self._payment('REF-HARDEN-EXCEPTION')
        with self.assertLogs('apps.payments.views', level='WARNING') as logs:
            with patch('apps.payments.views.requests.get') as mock_get:
                mock_get.side_effect = requests.exceptions.RequestException(
                    'exception-secret-marker'
                )
                response = self.client.get(
                    f'/api/payments/verify/{payment.reference}/'
                )

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.data, self.generic_error)
        self.assertNotIn('exception-secret-marker', str(response.data))
        self.assertIn('paystack verify unavailable', '\n'.join(logs.output))
        self.assertNotIn('exception-secret-marker', '\n'.join(logs.output))

    def test_webhook_logs_do_not_contain_raw_payload(self):
        payment = self._payment('REF-HARDEN-WEBHOOK')
        secret_marker = settings.PAYSTACK_SECRET_KEY
        payload = json.dumps({
            'event': 'charge.success',
            'data': {
                'reference': payment.reference,
                'amount': 350000,
                'metadata': {'authorization': secret_marker},
            },
        }).encode()
        signature = hmac.new(
            secret_marker.encode(), payload, hashlib.sha512
        ).hexdigest()

        with self.assertLogs('apps.payments.views', level='INFO') as logs:
            response = self.client.post(
                '/api/payments/webhook/',
                payload,
                content_type='application/json',
                HTTP_X_PAYSTACK_SIGNATURE=signature,
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn(secret_marker, '\n'.join(logs.output))
