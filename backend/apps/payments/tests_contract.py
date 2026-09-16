"""Agent 5 (QA) — contract-shape tests for Payments endpoints (§4).

M-1/M-2/M-3 were flagged by QA and are now FIXED to match the contract:
  * initiate returns {reference, checkout_url} (+ legacy authorization_url,
    message, payment kept for backward compatibility),
  * webhook returns {'received': True},
  * history items carry the contribution TITLE and a verified_at field.
"""
import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import patch

from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from apps.contributions.models import Contribution
from apps.payments.models import Payment
from apps.users.models import Department, User


class PaymentContractTests(APITestCase):
    def setUp(self):
        self.department = Department.objects.create(
            name='Computer Science', faculty='Physical Sciences'
        )
        self.student = User.objects.create_user(
            username='student1', email='student1@example.com',
            password='S7rong!Passw0rd', matric_number='CSC/2021/045',
            department=self.department, level='400',
        )
        self.contribution = Contribution.objects.create(
            department=self.department, created_by=self.student,
            title='Departmental Shirt 2026', description='',
            amount=Decimal('3500.00'),
            deadline=None, is_mandatory=True, target_level=None,
        )
        token, _ = Token.objects.get_or_create(user=self.student)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def _payment(self, reference, pay_status=Payment.STATUS_PENDING):
        return Payment.objects.create(
            student=self.student,
            contribution=self.contribution,
            payment_type=Payment.PAYMENT_CONTRIBUTION,
            amount=Decimal('3500.00'),
            reference=reference,
            status=pay_status,
            method=Payment.METHOD_ONLINE,
        )

    def test_initiate_matches_contract_shape(self):
        with patch('apps.payments.views.requests.post') as mock_post:
            mock_post.return_value.json.return_value = {
                'status': True,
                'data': {
                    'reference': 'PSK_TEST_001',
                    'authorization_url': 'https://checkout.paystack.com/x',
                },
            }
            response = self.client.post(
                '/api/payments/initiate/',
                {'contribution_id': self.contribution.id},
                format='json',
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Contract §4: {reference, checkout_url} — the frontend redirect key.
        self.assertEqual(response.data['reference'], 'PSK_TEST_001')
        self.assertEqual(
            response.data['checkout_url'], 'https://checkout.paystack.com/x'
        )
        # Backward-compatibility keys kept for earlier integrations.
        self.assertIn('authorization_url', response.data)
        self.assertIn('payment', response.data)
        # Kobo conversion still asserted (most common Paystack bug).
        self.assertEqual(
            mock_post.call_args.kwargs['json']['amount'], 350000
        )

    def test_webhook_matches_contract_shape(self):
        payment = self._payment('PSK_TEST_002')
        payload = json.dumps({
            'event': 'charge.success',
            'data': {'reference': 'PSK_TEST_002', 'amount': 350000},
        }).encode()
        from django.conf import settings
        signature = hmac.new(
            settings.PAYSTACK_SECRET_KEY.encode(), payload, hashlib.sha512,
        ).hexdigest()
        response = self.client.post(
            '/api/payments/webhook/',
            payload,
            content_type='application/json',
            HTTP_X_PAYSTACK_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'received': True})
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_SUCCESS)

    def test_verify_locks_current_shape(self):
        self._payment('PSK_TEST_003')
        with patch('apps.payments.views.requests.get') as mock_get:
            mock_get.return_value.json.return_value = {
                # Verify applies the same amount rule as the webhook, so the
                # mocked charge must report what it took (3500.00 in kobo).
                'status': True,
                'data': {'status': 'success', 'amount': 350000},
            }
            response = self.client.get('/api/payments/verify/PSK_TEST_003/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(response.data.keys()), {'message', 'payment'})
        self.assertEqual(response.data['payment']['status'], 'success')

    # --- robustness guards added during the post-fix re-review -------------

    def test_initiate_with_malformed_gateway_response_is_502(self):
        # A non-JSON gateway body (proxy error page) must not 500.
        with patch('apps.payments.views.requests.post') as mock_post:
            mock_post.return_value.json.side_effect = ValueError('not JSON')
            response = self.client.post(
                '/api/payments/initiate/',
                {'contribution_id': self.contribution.id},
                format='json',
            )
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.data['error'], 'gateway_unavailable')

    def test_initiate_with_non_numeric_contribution_id_is_400(self):
        response = self.client.post(
            '/api/payments/initiate/',
            {'contribution_id': 'not-a-number'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error'], 'bad_request')

    def test_verify_with_malformed_gateway_response_is_502(self):
        self._payment('PSK_TEST_007')
        with patch('apps.payments.views.requests.get') as mock_get:
            mock_get.return_value.json.side_effect = ValueError('not JSON')
            response = self.client.get('/api/payments/verify/PSK_TEST_007/')
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)

    def test_history_matches_contract_shape(self):
        self._payment('PSK_TEST_004', pay_status=Payment.STATUS_SUCCESS)
        response = self.client.get('/api/payments/history/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        item = response.data[0]
        # Contract §4: contribution is the fee TITLE + verified_at present.
        self.assertEqual(item['contribution'], 'Departmental Shirt 2026')
        self.assertIn('verified_at', item)
        self.assertIsNotNone(item['verified_at'])
        self.assertEqual(item['amount'], '3500.00')
        self.assertIsInstance(item['amount'], str)

    def test_history_pending_has_null_verified_at(self):
        self._payment('PSK_TEST_006', pay_status=Payment.STATUS_PENDING)
        response = self.client.get('/api/payments/history/')
        item = response.data[0]
        self.assertIsNone(item['verified_at'])

    def test_receipt_locks_current_shape(self):
        payment = self._payment(
            'PSK_TEST_005', pay_status=Payment.STATUS_SUCCESS
        )
        response = self.client.get(f'/api/payments/{payment.id}/receipt/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['reference'], 'PSK_TEST_005')
        self.assertEqual(response.data['status'], 'success')
        # Receipt renders the fee title too (same serializer).
        self.assertEqual(
            response.data['contribution'], 'Departmental Shirt 2026'
        )
