import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token

from apps.contributions.models import Contribution
from apps.users.models import Department
from .models import Payment


User = get_user_model()


class PaymentTests(APITestCase):

    def setUp(self):
        self.department = Department.objects.create(
            name='Computer Science',
            faculty='Physical Sciences',
        )
        self.user = User.objects.create_user(
            username='student1',
            email='student1@example.com',
            password='TestPassword123!',
            matric_number='TEST/2026/001',
            department=self.department,
            level='400',
        )
        self.other_user = User.objects.create_user(
            username='student2',
            email='student2@example.com',
            password='TestPassword123!',
            matric_number='TEST/2026/002',
            department=self.department,
            level='400',
        )
        self.admin = User.objects.create_user(
            username='admin1',
            email='admin@example.com',
            password='TestPassword123!',
            matric_number='TEST/2026/003',
            department=self.department,
            level='500',
            role='admin',
        )
        self.contribution = Contribution.objects.create(
            department=self.department,
            created_by=self.admin,
            title='Departmental Shirt 2026',
            amount=Decimal('3500.00'),
            deadline=timezone.now() + timezone.timedelta(days=30),
            is_mandatory=True,
            target_level=None,
        )
        self.token = Token.objects.create(user=self.user)
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Token {self.token.key}'
        )

    def _contribution_payload(self):
        return {'contribution_id': self.contribution.id}

    # --- history (own payments only) ---

    def test_unauthenticated_user_cannot_view_payments(self):
        self.client.credentials()
        response = self.client.get('/api/payments/history/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_student_can_only_view_their_own_payments(self):
        Payment.objects.create(
            student=self.user,
            contribution=self.contribution,
            payment_type=Payment.PAYMENT_DEPARTMENTAL_FEE,
            amount=Decimal('3500.00'),
            reference='REF-001',
        )
        Payment.objects.create(
            student=self.other_user,
            contribution=self.contribution,
            payment_type=Payment.PAYMENT_DEPARTMENTAL_FEE,
            amount=Decimal('3500.00'),
            reference='REF-002',
        )
        response = self.client.get('/api/payments/history/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['reference'], 'REF-001')

    # --- initiate (amount comes from the contribution, never the client) ---

    def test_initiate_requires_contribution_id(self):
        response = self.client.post(
            '/api/payments/initiate/',
            {},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_initiate_unknown_contribution_is_404(self):
        response = self.client.post(
            '/api/payments/initiate/',
            {'contribution_id': 99999},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch('apps.payments.views.requests.post')
    def test_initiate_uses_contribution_amount_not_client_amount(self, mock_post):
        mock_post.return_value.json.return_value = {
            'status': True,
            'data': {
                'reference': 'TEST-REFERENCE-001',
                'authorization_url': 'https://checkout.paystack.com/test',
            },
        }

        response = self.client.post(
            '/api/payments/initiate/',
            self._contribution_payload(),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # The amount sent to Paystack must be the contribution amount in kobo.
        sent = mock_post.call_args.kwargs['json']
        self.assertEqual(sent['amount'], 350000)  # 3500.00 * 100

        payment = Payment.objects.get(reference='TEST-REFERENCE-001')
        self.assertEqual(payment.student, self.user)
        self.assertEqual(payment.amount, Decimal('3500.00'))
        self.assertEqual(payment.contribution_id, self.contribution.id)
        self.assertEqual(payment.method, Payment.METHOD_ONLINE)
        self.assertEqual(payment.status, Payment.STATUS_PENDING)

    def test_initiate_already_paid_returns_409(self):
        Payment.objects.create(
            student=self.user,
            contribution=self.contribution,
            payment_type=Payment.PAYMENT_DEPARTMENTAL_FEE,
            amount=Decimal('3500.00'),
            reference='REF-001',
            status=Payment.STATUS_SUCCESS,
        )

        response = self.client.post(
            '/api/payments/initiate/',
            self._contribution_payload(),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['error'], 'already_paid')

    def test_initiate_student_outside_department_is_404(self):
        other_dept = Department.objects.create(name='Law', faculty='Law')
        stranger = User.objects.create_user(
            username='lawstudent',
            email='law@example.com',
            password='TestPassword123!',
            matric_number='LAW/2026/001',
            department=other_dept,
            level='100',
        )
        token = Token.objects.create(user=stranger)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        response = self.client.post(
            '/api/payments/initiate/',
            self._contribution_payload(),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # --- receipt (ownership-checked) ---

    def test_student_cannot_read_another_students_receipt(self):
        payment = Payment.objects.create(
            student=self.other_user,
            contribution=self.contribution,
            payment_type=Payment.PAYMENT_DEPARTMENTAL_FEE,
            amount=Decimal('3500.00'),
            reference='REF-OWN',
            status=Payment.STATUS_SUCCESS,
        )
        response = self.client.get(
            reverse('payment-receipt', args=[payment.id])
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_student_can_read_own_receipt(self):
        payment = Payment.objects.create(
            student=self.user,
            contribution=self.contribution,
            payment_type=Payment.PAYMENT_DEPARTMENTAL_FEE,
            amount=Decimal('3500.00'),
            reference='REF-MY',
            status=Payment.STATUS_SUCCESS,
        )
        response = self.client.get(
            reverse('payment-receipt', args=[payment.id])
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['reference'], 'REF-MY')

    # --- webhook (signature + amount + idempotency) ---

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
            'data': {'reference': 'REF-INVALID', 'amount': 350000},
        }).encode()

        response = self.client.post(
            '/api/payments/webhook/',
            payload,
            content_type='application/json',
            HTTP_X_PAYSTACK_SIGNATURE='invalid-signature',
        )

        # Contract §7: invalid signature → 400, not 401.
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_webhook_marks_payment_as_success_when_amount_matches(self):
        payment = Payment.objects.create(
            student=self.user,
            contribution=self.contribution,
            payment_type=Payment.PAYMENT_DEPARTMENTAL_FEE,
            amount=Decimal('3500.00'),
            reference='REF-WEBHOOK-001',
            status=Payment.STATUS_PENDING,
        )

        payload = json.dumps({
            'event': 'charge.success',
            'data': {
                'reference': payment.reference,
                'amount': 350000,
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
        self.assertEqual(payment.status, Payment.STATUS_SUCCESS)

    def test_webhook_rejects_amount_mismatch(self):
        payment = Payment.objects.create(
            student=self.user,
            contribution=self.contribution,
            payment_type=Payment.PAYMENT_DEPARTMENTAL_FEE,
            amount=Decimal('3500.00'),
            reference='REF-WEBHOOK-SHORT',
            status=Payment.STATUS_PENDING,
        )

        payload = json.dumps({
            'event': 'charge.success',
            'data': {
                'reference': payment.reference,
                # Intentionally too small: 3500 naira vs 35000 paid kobo.
                'amount': 35000,
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
        self.assertNotEqual(payment.status, Payment.STATUS_SUCCESS)

    def test_webhook_duplicate_success_is_idempotent(self):
        payment = Payment.objects.create(
            student=self.user,
            contribution=self.contribution,
            payment_type=Payment.PAYMENT_DEPARTMENTAL_FEE,
            amount=Decimal('3500.00'),
            reference='REF-WEBHOOK-DUP',
            status=Payment.STATUS_SUCCESS,
        )

        payload = json.dumps({
            'event': 'charge.success',
            'data': {
                'reference': payment.reference,
                'amount': 350000,
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
        self.assertEqual(payment.status, Payment.STATUS_SUCCESS)

    def _get_secret_key(self):
        from django.conf import settings
        return settings.PAYSTACK_SECRET_KEY.encode()