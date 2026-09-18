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
from .models import Payment, Transaction


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

    # --- verify: non-terminal gateway statuses must never mean "failed" ---

    def test_verify_keeps_abandoned_charge_pending(self):
        payment = Payment.objects.create(
            student=self.user,
            contribution=self.contribution,
            payment_type=Payment.PAYMENT_DEPARTMENTAL_FEE,
            amount=Decimal('3500.00'),
            reference='REF-VERIFY-ABANDONED',
            status=Payment.STATUS_PENDING,
        )

        # 'abandoned' = the student closed the checkout. That is not a decline,
        # and Paystack sends no webhook for it, so the row must stay pending
        # (retrying is already allowed) instead of flipping to failed.
        with patch('apps.payments.views.requests.get') as mock_get:
            mock_get.return_value.json.return_value = {
                'status': True,
                'data': {'status': 'abandoned'},
            }
            response = self.client.get(
                '/api/payments/verify/REF-VERIFY-ABANDONED/'
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PENDING)

    def test_verify_marks_a_declined_charge_failed(self):
        payment = Payment.objects.create(
            student=self.user,
            contribution=self.contribution,
            payment_type=Payment.PAYMENT_DEPARTMENTAL_FEE,
            amount=Decimal('3500.00'),
            reference='REF-VERIFY-DECLINED',
            status=Payment.STATUS_PENDING,
        )

        with patch('apps.payments.views.requests.get') as mock_get:
            mock_get.return_value.json.return_value = {
                'status': True,
                'data': {'status': 'failed'},
            }
            response = self.client.get(
                '/api/payments/verify/REF-VERIFY-DECLINED/'
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_FAILED)

    # --- verify: the amount rule must apply here too (money-hole guard) -----

    def test_verify_with_wrong_amount_does_not_credit_success(self):
        # The critical guard: verify used to trust Paystack's status alone, so a
        # student who paid a wrong amount got credited anyway whenever the
        # webhook was missed. It must apply the same amount rule as the webhook.
        payment = Payment.objects.create(
            student=self.user,
            contribution=self.contribution,
            payment_type=Payment.PAYMENT_DEPARTMENTAL_FEE,
            amount=Decimal('3500.00'),
            reference='REF-VERIFY-SHORT',
            status=Payment.STATUS_PENDING,
        )

        with patch('apps.payments.views.requests.get') as mock_get:
            mock_get.return_value.json.return_value = {
                'status': True,
                # 100.00 naira paid against a 3500.00 fee.
                'data': {'status': 'success', 'amount': 10000},
            }
            response = self.client.get('/api/payments/verify/REF-VERIFY-SHORT/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_FAILED)
        self.assertEqual(payment.paid_amount, Decimal('100.00'))
        self.assertEqual(
            payment.refund_status, Payment.REFUND_PENDING_REVIEW
        )

    def test_verify_with_correct_amount_credits_success(self):
        payment = Payment.objects.create(
            student=self.user,
            contribution=self.contribution,
            payment_type=Payment.PAYMENT_DEPARTMENTAL_FEE,
            amount=Decimal('3500.00'),
            reference='REF-VERIFY-EXACT',
            status=Payment.STATUS_PENDING,
        )

        with patch('apps.payments.views.requests.get') as mock_get:
            mock_get.return_value.json.return_value = {
                'status': True,
                'data': {'status': 'success', 'amount': 350000},
            }
            response = self.client.get('/api/payments/verify/REF-VERIFY-EXACT/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_SUCCESS)
        self.assertEqual(payment.refund_status, Payment.REFUND_NONE)

    # --- amount mismatch (either direction): failed + reviewed refund --------

    def _webhook(self, reference, amount_kobo):
        payload = json.dumps({
            'event': 'charge.success',
            'data': {'reference': reference, 'amount': amount_kobo},
        }).encode()
        signature = hmac.new(
            self._get_secret_key(), payload, hashlib.sha512
        ).hexdigest()
        return self.client.post(
            '/api/payments/webhook/',
            payload,
            content_type='application/json',
            HTTP_X_PAYSTACK_SIGNATURE=signature,
        )

    def _fee_payment(self, reference, amount='3500.00'):
        return Payment.objects.create(
            student=self.user,
            contribution=self.contribution,
            payment_type=Payment.PAYMENT_DEPARTMENTAL_FEE,
            amount=Decimal(amount),
            reference=reference,
            status=Payment.STATUS_PENDING,
        )

    def test_webhook_overpayment_is_failed_and_flagged_for_refund(self):
        payment = self._fee_payment('REF-OVERPAID')

        # 4000.00 paid against a 3500.00 fee: the real money is banked, so the
        # row must fail AND be flagged for a reviewed refund.
        response = self._webhook('REF-OVERPAID', 400000)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_FAILED)
        self.assertEqual(payment.paid_amount, Decimal('4000.00'))
        self.assertEqual(
            payment.refund_status, Payment.REFUND_PENDING_REVIEW
        )

    def test_webhook_underpayment_is_failed_and_flagged_for_refund(self):
        payment = self._fee_payment('REF-UNDERPAID')

        response = self._webhook('REF-UNDERPAID', 100000)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_FAILED)
        self.assertEqual(payment.paid_amount, Decimal('1000.00'))
        self.assertEqual(
            payment.refund_status, Payment.REFUND_PENDING_REVIEW
        )

    def test_webhook_exact_amount_records_paid_amount_without_refund(self):
        payment = self._fee_payment('REF-EXACT')

        self._webhook('REF-EXACT', 350000)

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_SUCCESS)
        self.assertEqual(payment.paid_amount, Decimal('3500.00'))
        self.assertEqual(payment.refund_status, Payment.REFUND_NONE)

    def test_webhook_second_charge_for_same_fee_is_flagged_not_credited(self):
        # Two checkouts can be open at once (contract §4 allows a new attempt),
        # and a student can pay both. The fee is credited once; the extra real
        # charge is failed and flagged so the money gets returned.
        first = self._fee_payment('REF-DUP-FIRST')
        second = self._fee_payment('REF-DUP-SECOND')

        self._webhook('REF-DUP-FIRST', 350000)
        self._webhook('REF-DUP-SECOND', 350000)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, Payment.STATUS_SUCCESS)
        self.assertEqual(first.refund_status, Payment.REFUND_NONE)
        self.assertEqual(second.status, Payment.STATUS_FAILED)
        self.assertEqual(second.paid_amount, Decimal('3500.00'))
        self.assertEqual(
            second.refund_status, Payment.REFUND_PENDING_REVIEW
        )

    def test_reviewed_refund_flag_is_never_overwritten_by_a_retry(self):
        # A reviewer's decision must survive Paystack retrying the webhook —
        # otherwise a refunded charge looks unrefunded and could be refunded
        # twice.
        payment = self._fee_payment('REF-REFUNDED')
        self._webhook('REF-REFUNDED', 400000)

        payment.refresh_from_db()
        payment.refund_status = Payment.REFUND_REFUNDED
        payment.save(update_fields=['refund_status'])

        self._webhook('REF-REFUNDED', 400000)
        self._webhook('REF-REFUNDED', 400000)

        payment.refresh_from_db()
        self.assertEqual(payment.refund_status, Payment.REFUND_REFUNDED)
        self.assertEqual(payment.paid_amount, Decimal('4000.00'))

    def test_initiate_rejects_an_expired_contribution(self):
        # The student list hides expired fees; initiate must too, or a stale id
        # lets money be taken for a collection nobody is accepting.
        expired = Contribution.objects.create(
            department=self.department,
            created_by=self.user,
            title='Expired Dues',
            amount=Decimal('1000.00'),
            deadline=timezone.now() - timezone.timedelta(days=1),
            target_level=None,
        )

        with patch('apps.payments.views.requests.post') as mock_post:
            response = self.client.post(
                '/api/payments/initiate/',
                {'contribution_id': expired.id},
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_post.assert_not_called()

    def _get_secret_key(self):
        from django.conf import settings
        return settings.PAYSTACK_SECRET_KEY.encode()

    # --- unverified (admin refund-review queue; read-only, additive) ---

    def _flagged_payment(self, reference, received_kobo):
        """A failed, refund-flagged row as the settlement rule would leave it."""
        payment = Payment.objects.create(
            student=self.user,
            contribution=self.contribution,
            payment_type=Payment.PAYMENT_CONTRIBUTION,
            amount=Decimal('3500.00'),
            reference=reference,
            status=Payment.STATUS_FAILED,
            paid_amount=(Decimal(received_kobo) / 100),
        )
        payment.refund_status = Payment.REFUND_PENDING_REVIEW
        payment.save(update_fields=['refund_status'])
        return payment

    def test_admin_sees_unverified_payments(self):
        self._flagged_payment('REF-FLAG-001', 400000)  # overpaid: 4000 vs 3500

        self.client.credentials(
            HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=self.admin).key}'
        )
        response = self.client.get('/api/payments/unverified/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        row = response.data['results'][0]
        self.assertEqual(row['reference'], 'REF-FLAG-001')
        self.assertEqual(row['student_matric'], 'TEST/2026/001')
        self.assertEqual(row['contribution_title'], 'Departmental Shirt 2026')
        self.assertEqual(row['expected_amount'], '3500.00')
        self.assertEqual(row['amount_received'], '4000.00')
        self.assertEqual(row['refund_status'], Payment.REFUND_PENDING_REVIEW)
        # Mismatch wording: direction + both amounts + the difference.
        self.assertIn('overpaid', row['mismatch_detail'])
        self.assertIn('4,000.00', row['mismatch_detail'])
        self.assertIn('3,500.00', row['mismatch_detail'])
        self.assertIn('500.00', row['mismatch_detail'])

    def test_student_cannot_see_unverified_payments(self):
        response = self.client.get('/api/payments/unverified/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unverified_empty_when_all_verified(self):
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=self.admin).key}'
        )
        response = self.client.get('/api/payments/unverified/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)
        self.assertEqual(response.data['results'], [])

    # --- deployment hardening: race-proof settlement, webhook proof, health ---

    def test_concurrent_webhook_and_verify_cannot_double_credit(self):
        # The race: a webhook delivery and a student hitting verify both apply
        # a valid charge for the same fee, and the second one to save would
        # previously double-credit. With the DB constraint the loser lands in
        # refund review — the money is queued for a human, never counted twice.
        payment_a = self._fee_payment('REF-RACE-A')
        payment_b = self._fee_payment('REF-RACE-B')

        self._webhook('REF-RACE-A', 350000)

        with patch('apps.payments.views.requests.get') as mock_get:
            mock_get.return_value.json.return_value = {
                'status': True,
                'data': {'status': 'success', 'amount': 350000},
            }
            self.client.get('/api/payments/verify/REF-RACE-B/')

        payment_a.refresh_from_db()
        payment_b.refresh_from_db()
        credited = [p for p in (payment_a, payment_b) if p.status == Payment.STATUS_SUCCESS]
        flagged = [p for p in (payment_a, payment_b) if p.refund_status == Payment.REFUND_PENDING_REVIEW]
        self.assertEqual(len(credited), 1, 'two payments credited for one fee — double-count!')
        self.assertEqual(len(flagged), 1)
        # Collected totals must reflect ONE payment, not two.
        self.assertEqual(self.contribution.total_collected(), Decimal('3500.00'))

    def test_webhook_persists_the_raw_gateway_payload(self):
        # Audit proof: what Paystack said must be on file verbatim, once per
        # reference even across retries, for refund disputes.
        self.assertEqual(Transaction.objects.count(), 0)
        self._webhook('REF-PROOF-001', 350000)
        self._webhook('REF-PROOF-001', 350000)  # retry

        self.assertEqual(Transaction.objects.count(), 1)
        proof = Transaction.objects.get(reference='REF-PROOF-001')
        self.assertEqual(proof.raw_payload['event'], 'charge.success')
        self.assertEqual(proof.raw_payload['data']['amount'], 350000)

    def test_webhook_keeps_proof_even_for_an_unknown_reference(self):
        self._webhook('REF-UNKNOWN-999', 350000)

        proof = Transaction.objects.filter(reference='REF-UNKNOWN-999').first()
        self.assertIsNotNone(proof)
        self.assertIsNone(proof.payment)  # no matching Payment row — still kept

    def test_health_endpoint_reports_database_status(self):
        from django.db import connection

        self.client.credentials()
        response = self.client.get('/api/health/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
        self.assertEqual(response.json()['database'], 'up')
        self.assertTrue(connection.is_usable())