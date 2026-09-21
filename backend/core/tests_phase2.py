"""Phase 2 frontend integration readiness tests."""
from datetime import timedelta
from decimal import Decimal
import hashlib
import hmac
import json
from unittest.mock import patch

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from apps.contributions.models import Contribution
from apps.notifications.models import Notification
from apps.payments.models import Payment
from apps.users.models import Department, User


class FrontendErrorContractTests(APITestCase):
    """Lock the one error shape consumed by frontend API clients."""

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

    @staticmethod
    def _assert_standard_error(response, expected_error, expected_status):
        response.render()
        payload = response.json()
        assert response.status_code == expected_status, payload
        assert set(payload.keys()) == {'error', 'message'}
        assert payload['error'] == expected_error, payload
        assert isinstance(payload['message'], str)
        assert payload['message']

    def test_unauthenticated_protected_routes_use_standard_401(self):
        protected_requests = [
            self.client.get('/api/auth/me/'),
            self.client.get('/api/contributions/'),
            self.client.get('/api/contributions/999/'),
            self.client.get('/api/contributions/999/summary/'),
            self.client.get('/api/contributions/1/payments/'),
            self.client.post('/api/contributions/', {}, format='json'),
            self.client.post('/api/payments/initiate/', {}, format='json'),
            self.client.get('/api/payments/history/'),
            self.client.get('/api/payments/verify/UNKNOWN-REF/'),
            self.client.get('/api/payments/1/receipt/'),
            self.client.get('/api/payments/unverified/'),
            self.client.get('/api/notifications/'),
            self.client.post('/api/notifications/1/read/'),
            self.client.post('/api/auth/logout/'),
            self.client.post('/api/auth/users/1/set-role/', {}, format='json'),
        ]

        for response in protected_requests:
            self._assert_standard_error(response, 'unauthorized', 401)

    def test_invalid_token_is_indistinguishable_from_missing_token(self):
        self.client.credentials(HTTP_AUTHORIZATION='Token not-a-real-token')
        response = self.client.get('/api/auth/me/')

        self._assert_standard_error(response, 'unauthorized', 401)

    def test_student_forbidden_routes_use_standard_403(self):
        self.client.credentials(
            HTTP_AUTHORIZATION='Token '
            + Token.objects.get_or_create(user=self.student)[0].key
        )
        forbidden_requests = [
            self.client.post('/api/contributions/', {}, format='json'),
            self.client.get('/api/contributions/1/payments/'),
            self.client.get('/api/payments/unverified/'),
            self.client.post('/api/auth/import/'),
            self.client.post('/api/auth/reset-code/', {}, format='json'),
            self.client.post(
                '/api/auth/users/1/set-role/', {}, format='json'
            ),
        ]

        for response in forbidden_requests:
            self._assert_standard_error(response, 'permission_denied', 403)

    def _authenticate(self, user):
        self.client.credentials(
            HTTP_AUTHORIZATION='Token '
            + Token.objects.get_or_create(user=user)[0].key
        )

    def test_authenticated_unknown_resources_use_standard_404(self):
        """A bad id must arrive renderable — not as a bare sentence."""
        self._authenticate(self.student)
        not_found_requests = [
            self.client.get('/api/contributions/999/'),
            self.client.get('/api/contributions/999/summary/'),
            self.client.get('/api/payments/999/receipt/'),
            # v2.1 fix: this used to return {'error': 'Payment not found.'}
            # with no `message`, which rendered as an empty toast.
            self.client.get('/api/payments/verify/NO-SUCH-REFERENCE/'),
            self.client.post('/api/notifications/999/read/'),
            self.client.post(
                '/api/payments/initiate/',
                {'contribution_id': 999},
                format='json',
            ),
        ]

        for response in not_found_requests:
            self._assert_standard_error(response, 'not_found', 404)

    def test_expired_fee_is_not_found_not_payable(self):
        """
        An expired fee is *invisible* to the student (same rule as the list),
        so initiate answers 404 not_found — never a 400 that hints the fee
        exists, and never a checkout the department has stopped accepting.
        """
        expired = Contribution.objects.create(
            department=self.department,
            created_by=self.student,
            title='Expired Dues',
            amount=Decimal('1000.00'),
            deadline=timezone.now() - timedelta(days=1),
        )
        self._authenticate(self.student)

        response = self.client.post(
            '/api/payments/initiate/',
            {'contribution_id': expired.id},
            format='json',
        )

        self._assert_standard_error(response, 'not_found', 404)

    def test_malformed_payment_requests_use_standard_400(self):
        self._authenticate(self.student)
        bad_requests = [
            self.client.post('/api/payments/initiate/', {}, format='json'),
            self.client.post(
                '/api/payments/initiate/',
                {'contribution_id': 'not-a-number'},
                format='json',
            ),
        ]

        for response in bad_requests:
            self._assert_standard_error(response, 'bad_request', 400)

    def test_duplicate_payment_uses_standard_409(self):
        contribution = Contribution.objects.create(
            department=self.department,
            created_by=self.student,
            title='Excursion Fee',
            amount=Decimal('5000.00'),
        )
        Payment.objects.create(
            student=self.student,
            contribution=contribution,
            payment_type=Payment.PAYMENT_CONTRIBUTION,
            amount=Decimal('5000.00'),
            reference='PSK-ERROR-CONTRACT',
            status=Payment.STATUS_SUCCESS,
        )
        self._authenticate(self.student)

        response = self.client.post(
            '/api/payments/initiate/',
            {'contribution_id': contribution.id},
            format='json',
        )

        self._assert_standard_error(response, 'already_paid', 409)

    def test_unparseable_bodies_use_standard_error_shape(self):
        """
        DRF raises ParseError (and UnsupportedMediaType) with a `detail` key.
        §7 promises *one* shape, so the exception handler must normalise these
        too — a frontend whose single handler reads `message` would otherwise
        render nothing when a client sends a broken body.
        """
        self._authenticate(self.student)
        # (status, expected `error` code) — DRF's own exception classes each
        # carry a `detail` whose `.code` the handler passes through.
        broken_requests = [
            # Malformed JSON on a normal endpoint.
            (
                self.client.post(
                    '/api/payments/initiate/',
                    '{not valid json',
                    content_type='application/json',
                ),
                status.HTTP_400_BAD_REQUEST,
                'parse_error',
            ),
            # A format the endpoint can't parse at all.
            (
                self.client.post(
                    '/api/payments/initiate/',
                    'contribution_id=1',
                    content_type='text/plain',
                ),
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                'unsupported_media_type',
            ),
        ]

        for response, expected_status, expected_code in broken_requests:
            self._assert_standard_error(
                response, expected_code, expected_status
            )

    def test_webhook_rejections_use_standard_error_shape(self):
        """
        The gateway only reads the status code, but §7 promises the same
        {error, message} body on *every* endpoint — so an ops client that
        replays a bad delivery gets a parseable error too.
        """
        secret = settings.PAYSTACK_SECRET_KEY.encode()

        def signed(body):
            return hmac.new(secret, body, hashlib.sha512).hexdigest()

        non_json = b'not-json'
        non_dict = json.dumps(['not-a-dict']).encode()
        rejected_requests = [
            # No signature header at all.
            self.client.post(
                '/api/payments/webhook/', b'{}', content_type='application/json'
            ),
            # Wrong signature.
            self.client.post(
                '/api/payments/webhook/',
                b'{}',
                content_type='application/json',
                HTTP_X_PAYSTACK_SIGNATURE='invalid-signature',
            ),
            # Correctly signed, but the body is not JSON.
            self.client.post(
                '/api/payments/webhook/',
                non_json,
                content_type='application/json',
                HTTP_X_PAYSTACK_SIGNATURE=signed(non_json),
            ),
            # Correctly signed, JSON, but not an object.
            self.client.post(
                '/api/payments/webhook/',
                non_dict,
                content_type='application/json',
                HTTP_X_PAYSTACK_SIGNATURE=signed(non_dict),
            ),
        ]

        for response in rejected_requests:
            self._assert_standard_error(response, 'bad_request', 400)

    def test_wrong_method_uses_standard_405(self):
        self._authenticate(self.student)

        response = self.client.put('/api/payments/initiate/', {}, format='json')

        self._assert_standard_error(response, 'method_not_allowed', 405)

    def test_login_rate_limit_uses_standard_429(self):
        """
        10/min per IP on login/register. The student sees a real message (not a
        blank "429 error"), and the limit must not break normal login attempts.
        """
        from django.core.cache import cache

        cache.clear()  # throttle counters live in the cache
        self.addCleanup(cache.clear)

        url = '/api/auth/login/'
        payload = {'username': 'nobody', 'password': 'wrong-password'}

        # The first 10 are answered normally (a plain 400 auth failure, §1)…
        for _ in range(10):
            response = self.client.post(url, payload, format='json')
            assert response.status_code == 400, response.status_code

        # …the 11th is throttled, in the same {error, message} shape.
        response = self.client.post(url, payload, format='json')
        self._assert_standard_error(response, 'throttled', 429)


class CorsFrontendIntegrationTests(APITestCase):
    def _first_frontend_origin(self):
        return settings.CORS_ALLOWED_ORIGINS[0]

    def test_vite_dev_origin_is_allowlisted(self):
        """
        The real frontend (dept-payments, Vite) calls us cross-origin from
        http://localhost:5173 — if the allowlist default ever drifts away
        from that, every browser request dies at preflight. Pin it.
        """
        self.assertIn('http://localhost:5173', settings.CORS_ALLOWED_ORIGINS)
        self.assertIn(
            'http://127.0.0.1:5173', settings.CORS_ALLOWED_ORIGINS
        )

    def test_browser_preflight_allows_frontend_json_and_auth(self):
        response = self.client.options(
            '/api/auth/me/',
            HTTP_ORIGIN=self._first_frontend_origin(),
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='PATCH',
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS='authorization,content-type',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Access-Control-Allow-Origin'],
            self._first_frontend_origin(),
        )

        allowed_methods = {
            method.strip().upper()
            for method in response['Access-Control-Allow-Methods'].split(',')
        }
        self.assertIn('PATCH', allowed_methods)
        self.assertIn('GET', allowed_methods)
        self.assertIn('POST', allowed_methods)

        allowed_headers = {
            header.strip().lower()
            for header in response['Access-Control-Allow-Headers'].split(',')
        }
        self.assertIn('authorization', allowed_headers)
        self.assertIn('content-type', allowed_headers)

    def test_simple_frontend_request_receives_allowed_origin(self):
        response = self.client.get(
            '/api/departments/',
            HTTP_ORIGIN=self._first_frontend_origin(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Access-Control-Allow-Origin'],
            self._first_frontend_origin(),
        )

    def test_disallowed_origin_does_not_receive_cors_approval(self):
        response = self.client.get(
            '/api/departments/',
            HTTP_ORIGIN='https://untrusted.example.com',
        )

        self.assertNotIn('Access-Control-Allow-Origin', response)


class FrontendPaymentFlowIntegrationTests(APITestCase):
    """One browser-like happy path across users, contributions, and payments."""

    def setUp(self):
        self.department = Department.objects.create(
            name='Computer Science', faculty='Physical Sciences'
        )
        self.rep = User.objects.create_user(
            username='class.rep',
            email='class.rep@example.com',
            password='S7rong!Passw0rd',
            matric_number='CSC/2026/003',
            department=self.department,
            level='500',
            role=User.ROLE_CLASS_REP,
        )
        self.student = User.objects.create_user(
            username='frontend.student',
            email='frontend.student@example.com',
            password='S7rong!Passw0rd',
            matric_number='CSC/2026/045',
            department=self.department,
            level='400',
        )

    def _login(self, user, matric=None):
        response = self.client.post(
            '/api/auth/login/',
            {
                'matric_number': matric or user.matric_number.lower(),
                'password': 'S7rong!Passw0rd',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data['token']

    def _create_contribution(self, rep_token):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + rep_token)
        response = self.client.post(
            '/api/contributions/',
            {
                'title': 'Excursion Fee',
                'description': 'End of year excursion',
                'amount': '5000.00',
                'deadline': '2026-12-15T23:59:00Z',
                'is_mandatory': True,
                'target_level': '400',
            },
            format='json',
            HTTP_AUTHORIZATION='Token ' + rep_token,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            set(response.data.keys()),
            {
                'id', 'title', 'description', 'amount', 'deadline',
                'is_mandatory', 'target_level', 'is_closed', 'has_paid',
                'created_at', 'department_id',
            },
        )
        self.assertEqual(response.data['department_id'], self.department.id)
        return response.data['id']

    def test_frontend_payment_happy_path(self):
        department_response = self.client.get('/api/departments/')
        self.assertEqual(department_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(department_response.data), 1)
        self.assertEqual(
            set(department_response.data[0].keys()),
            {'id', 'name', 'faculty'},
        )

        student_token = self._login(self.student, 'csc/2026/045')
        rep_token = self._login(self.rep)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + student_token)

        contribution_id = self._create_contribution(rep_token)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + student_token)

        list_response = self.client.get('/api/contributions/')
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_response.data), 1)
        contribution = list_response.data[0]
        self.assertEqual(contribution['id'], contribution_id)
        self.assertEqual(contribution['title'], 'Excursion Fee')
        self.assertEqual(contribution['amount'], '5000.00')
        self.assertIs(contribution['has_paid'], False)
        self.assertEqual(
            set(contribution.keys()),
            {
                'id', 'title', 'description', 'amount', 'deadline',
                'is_mandatory', 'target_level', 'is_closed', 'has_paid',
                'created_at',
            },
        )

        detail_response = self.client.get(
            f'/api/contributions/{contribution_id}/'
        )
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.data['id'], contribution_id)

        with patch('apps.payments.views.requests.post') as mock_post:
            mock_post.return_value.json.return_value = {
                'status': True,
                'data': {
                    'reference': 'PSK-FRONTEND-E2E',
                    'authorization_url': 'https://checkout.paystack.com/frontend-e2e',
                },
            }
            initiate_response = self.client.post(
                '/api/payments/initiate/',
                {'contribution_id': contribution_id},
                format='json',
            )

        self.assertEqual(initiate_response.status_code, status.HTTP_200_OK)
        self.assertIn('reference', initiate_response.data)
        self.assertIn('checkout_url', initiate_response.data)
        self.assertEqual(
            initiate_response.data['checkout_url'],
            'https://checkout.paystack.com/frontend-e2e',
        )
        sent_to_gateway = mock_post.call_args.kwargs['json']
        self.assertEqual(sent_to_gateway['amount'], 500000)
        self.assertEqual(sent_to_gateway['email'], self.student.email)

        payment_id = initiate_response.data['payment']['id']
        payment = Payment.objects.get(reference='PSK-FRONTEND-E2E')
        self.assertEqual(payment.id, payment_id)
        self.assertEqual(payment.status, Payment.STATUS_PENDING)
        self.assertEqual(payment.amount, Decimal('5000.00'))

        payload = json.dumps({
            'event': 'charge.success',
            'data': {
                'reference': 'PSK-FRONTEND-E2E',
                'amount': 500000,
            },
        }).encode()
        signature = hmac.new(
            settings.PAYSTACK_SECRET_KEY.encode(), payload, hashlib.sha512
        ).hexdigest()
        webhook_response = self.client.post(
            '/api/payments/webhook/',
            payload,
            content_type='application/json',
            HTTP_X_PAYSTACK_SIGNATURE=signature,
        )

        self.assertEqual(webhook_response.status_code, status.HTTP_200_OK)
        self.assertEqual(webhook_response.data, {'received': True})
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_SUCCESS)
        self.assertEqual(payment.paid_amount, Decimal('5000.00'))
        self.assertIsNotNone(payment.contribution_id)

        history_response = self.client.get('/api/payments/history/')
        self.assertEqual(history_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(history_response.data), 1)
        history_item = history_response.data[0]
        self.assertEqual(history_item['reference'], 'PSK-FRONTEND-E2E')
        self.assertEqual(history_item['contribution'], 'Excursion Fee')
        self.assertEqual(history_item['amount'], '5000.00')
        self.assertEqual(history_item['status'], Payment.STATUS_SUCCESS)
        self.assertIsNotNone(history_item['verified_at'])
        self.assertEqual(
            set(history_item.keys()),
            {
                'id', 'student', 'contribution', 'payment_type', 'amount',
                'paid_amount', 'reference', 'status', 'refund_status',
                'method', 'created_at', 'updated_at', 'verified_at',
            },
        )

        receipt_response = self.client.get(
            f'/api/payments/{payment_id}/receipt/'
        )
        self.assertEqual(receipt_response.status_code, status.HTTP_200_OK)
        self.assertEqual(receipt_response.data['reference'], 'PSK-FRONTEND-E2E')
        self.assertEqual(receipt_response.data['status'], Payment.STATUS_SUCCESS)

        summary_response = self.client.get(
            f'/api/contributions/{contribution_id}/summary/'
        )
        self.assertEqual(summary_response.status_code, status.HTTP_200_OK)
        self.assertEqual(summary_response.data['total_expected'], '5000.00')
        self.assertEqual(summary_response.data['total_collected'], '5000.00')
        self.assertEqual(summary_response.data['outstanding_count'], 0)

        self.client.credentials(HTTP_AUTHORIZATION='Token ' + rep_token)
        roster_response = self.client.get(
            f'/api/contributions/{contribution_id}/payments/',
        )
        self.assertEqual(roster_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(roster_response.data), 1)
        self.assertEqual(roster_response.data[0]['student'], 'frontend.student')
        self.assertEqual(
            roster_response.data[0]['matric_number'], 'CSC/2026/045'
        )
        self.assertEqual(roster_response.data[0]['status'], 'success')
        self.assertEqual(
            set(roster_response.data[0].keys()),
            {'student', 'matric_number', 'status', 'paid_at'},
        )

        self.client.credentials(HTTP_AUTHORIZATION='Token ' + student_token)
        notification_response = self.client.get('/api/notifications/')
        self.assertEqual(notification_response.status_code, status.HTTP_200_OK)
        payment_alert = next(
            row for row in notification_response.data
            if row['notification_type'] == Notification.TYPE_PAYMENT_SUCCESS
        )
        self.assertEqual(payment_alert['contribution_title'], 'Excursion Fee')
        self.assertFalse(payment_alert['is_read'])
        self.assertEqual(
            set(payment_alert.keys()),
            {
                'id', 'notification_type', 'message', 'contribution',
                'contribution_title', 'is_read', 'created_at',
            },
        )

        mark_read_response = self.client.post(
            f'/api/notifications/{payment_alert["id"]}/read/'
        )
        self.assertEqual(mark_read_response.status_code, status.HTTP_200_OK)
        self.assertTrue(mark_read_response.data['is_read'])
