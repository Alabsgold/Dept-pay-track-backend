import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from apps.contributions.models import Contribution
from apps.payments.models import Payment
from apps.users.models import Department

from .models import Notification

User = get_user_model()


class NotificationTests(APITestCase):
    """
    Locks API_CONTRACT.md §5 (list + mark-read) and the auto-generation rules
    from AGENTS.md Agent 4: a notification is created on payment success/
    failure transitions and on new contribution creation — exactly once.
    """

    def setUp(self):
        self.department = Department.objects.create(
            name='Computer Science',
            faculty='Physical Sciences',
        )
        self.other_department = Department.objects.create(
            name='Law',
            faculty='Law',
        )
        self.student = self._u(
            'student1', 'student1@example.com', 'TEST/2026/001',
            self.department, '400',
        )
        self.other_student = self._u(
            'student2', 'student2@example.com', 'TEST/2026/002',
            self.department, '400',
        )
        self.level100 = self._u(
            'level100', 'level100@example.com', 'TEST/2026/003',
            self.department, '100',
        )
        self.stranger = self._u(
            'lawstudent', 'law@example.com', 'LAW/2026/001',
            self.other_department, '400',
        )
        self.rep = self._u(
            'rep1', 'rep@example.com', 'TEST/2026/004',
            self.department, '500', User.ROLE_CLASS_REP,
        )
        self.admin = self._u(
            'admin1', 'admin@example.com', 'TEST/2026/005',
            self.department, '500', User.ROLE_ADMIN,
        )
        self.contribution = Contribution.objects.create(
            department=self.department,
            created_by=self.rep,
            title='Departmental Shirt 2026',
            description='Official shirt',
            amount=Decimal('3500.00'),
            deadline=timezone.now() + timedelta(days=30),
            is_mandatory=True,
            target_level=None,
        )
        self._auth(self.student)

    # --- helpers ---------------------------------------------------------

    def _u(self, username, email, matric, dept, level, role=User.ROLE_STUDENT):
        return User.objects.create_user(
            username=username,
            email=email,
            password='S7rong!Passw0rd',
            matric_number=matric,
            department=dept,
            level=level,
            role=role,
        )

    def _auth(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def _notification(self, recipient, ntype=Notification.TYPE_PAYMENT_SUCCESS,
                      contribution=None, is_read=False):
        return Notification.objects.create(
            recipient=recipient,
            notification_type=ntype,
            message='Test alert message.',
            contribution=contribution,
            is_read=is_read,
        )

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

    def _signed_webhook(self, reference, amount_kobo=350000):
        payload = json.dumps({
            'event': 'charge.success',
            'data': {'reference': reference, 'amount': amount_kobo},
        }).encode()
        from django.conf import settings
        signature = hmac.new(
            settings.PAYSTACK_SECRET_KEY.encode(),
            payload,
            hashlib.sha512,
        ).hexdigest()
        return payload, signature

    def _clean(self):
        """Wipe setUp's fan-out so tests count exactly what they create."""
        Notification.objects.all().delete()

    # --- endpoints: auth + ownership --------------------------------------

    def test_unauthenticated_cannot_list_notifications(self):
        self.client.credentials()
        response = self.client.get('/api/notifications/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unauthenticated_cannot_mark_read(self):
        self.client.credentials()
        notification = self._notification(self.student)
        response = self.client.post(
            f'/api/notifications/{notification.id}/read/'
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_returns_only_own_notifications(self):
        self._clean()
        mine = self._notification(self.student)
        self._notification(self.other_student)
        self._notification(self.stranger)

        response = self.client.get('/api/notifications/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], mine.id)

    def test_list_is_ordered_newest_first(self):
        self._clean()
        older = self._notification(self.student)
        newer = self._notification(self.student)

        response = self.client.get('/api/notifications/')

        ids = [row['id'] for row in response.data]
        self.assertEqual(ids, [newer.id, older.id])

    def test_mark_other_users_notification_returns_404(self):
        # Existence-leak rule: 404 (not 403) for other users' rows.
        foreign = self._notification(self.other_student)
        response = self.client.post(
            f'/api/notifications/{foreign.id}/read/'
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        foreign.refresh_from_db()
        self.assertFalse(foreign.is_read)

    def test_mark_read_is_idempotent(self):
        self._clean()
        notification = self._notification(self.student)

        first = self.client.post(f'/api/notifications/{notification.id}/read/')
        second = self.client.post(f'/api/notifications/{notification.id}/read/')

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(Notification.objects.count(), 1)
        self.assertTrue(second.data['is_read'])

    def test_mark_read_persists_in_list(self):
        notification = self._notification(self.student)
        self.client.post(f'/api/notifications/{notification.id}/read/')

        response = self.client.get('/api/notifications/')

        self.assertEqual(response.data[0]['is_read'], True)

    # --- payment transitions (webhook / verify / manual) -------------------

    def test_webhook_success_creates_one_payment_success_notification(self):
        self._clean()
        payment = self._payment('REF-NOTIF-001')
        payload, signature = self._signed_webhook('REF-NOTIF-001')

        response = self.client.post(
            '/api/payments/webhook/',
            payload,
            content_type='application/json',
            HTTP_X_PAYSTACK_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        alerts = Notification.objects.filter(
            recipient=self.student,
            notification_type=Notification.TYPE_PAYMENT_SUCCESS,
        )
        self.assertEqual(alerts.count(), 1)
        alert = alerts.first()

    def test_reverify_does_not_duplicate_notification(self):
        # A payment already banked as success: the webhook's idempotent
        # early-return and the verify endpoint's re-save must both stay
        # silent — one alert per transition, never per save.
        self._clean()
        self._payment('REF-NOTIF-DUP', pay_status=Payment.STATUS_SUCCESS)
        self.assertEqual(
            Notification.objects.filter(
                notification_type=Notification.TYPE_PAYMENT_SUCCESS
            ).count(),
            1,
        )

        with patch('apps.payments.views.requests.get') as mock_get:
            mock_get.return_value.json.return_value = {
                'status': True,
                'data': {'status': 'success'},
            }
            verify = self.client.get('/api/payments/verify/REF-NOTIF-DUP/')

        self.assertEqual(verify.status_code, status.HTTP_200_OK)
        self.assertEqual(
            Notification.objects.filter(
                notification_type=Notification.TYPE_PAYMENT_SUCCESS
            ).count(),
            1,
        )

    def test_webhook_amount_mismatch_creates_failed_notification_once(self):
        self._clean()
        payment = self._payment('REF-NOTIF-SHORT')
        # 35000 kobo ≠ 3500.00 naira → the webhook must mark it failed.
        payload, signature = self._signed_webhook('REF-NOTIF-SHORT', 35000)

        for _ in range(2):  # Paystack retries the same mismatched webhook
            response = self.client.post(
                '/api/payments/webhook/',
                payload,
                content_type='application/json',
                HTTP_X_PAYSTACK_SIGNATURE=signature,
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_FAILED)
        alerts = Notification.objects.filter(
            recipient=self.student,
            notification_type=Notification.TYPE_PAYMENT_FAILED,
        )
        self.assertEqual(alerts.count(), 1)
        self.assertIn('failed', alerts.first().message)

    def test_manual_mark_paid_notifies_student(self):
        # The bridge banks the payment directly as success → the created-path
        # in the signal must announce it too.
        self._clean()
        self._auth(self.admin)
        response = self.client.post(
            f'/api/contributions/{self.contribution.id}/payments/',
            {'matric_number': 'TEST/2026/001'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        alerts = Notification.objects.filter(
            recipient=self.student,
            notification_type=Notification.TYPE_PAYMENT_SUCCESS,
        )
        self.assertEqual(alerts.count(), 1)

    # --- new contribution fan-out ------------------------------------------

    def test_new_contribution_notifies_eligible_students_only(self):
        self._clean()
        self._auth(self.rep)
        response = self.client.post(
            '/api/contributions/',
            {
                'title': 'Excursion Fee',
                'description': 'End of year excursion',
                'amount': '5000.00',
                'deadline': '2026-10-15T23:59:00Z',
                'is_mandatory': True,
                'target_level': '400',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        contribution = Contribution.objects.get(title='Excursion Fee')
        alerts = Notification.objects.filter(
            notification_type=Notification.TYPE_NEW_CONTRIBUTION,
        )
        recipients = {a.recipient_id for a in alerts}
        # 400-level students in the rep's department only — not level 100,
        # not the Law student (eligible_students respects target_level).
        self.assertEqual(recipients, {self.student.id, self.other_student.id})
        alert = alerts.filter(recipient=self.student).first()
        self.assertEqual(alert.contribution_id, contribution.id)
        self.assertIn('Excursion Fee', alert.message)

    def test_new_contribution_message_is_truncated_to_255(self):
        self._clean()
        Contribution.objects.create(
            department=self.department,
            created_by=self.rep,
            title='L' * 200,  # max-length title
            amount=Decimal('12345.67'),
            deadline=None,
            is_mandatory=False,
            target_level=None,
        )
        alert = Notification.objects.filter(
            recipient=self.student,
            notification_type=Notification.TYPE_NEW_CONTRIBUTION,
        ).first()
        self.assertIsNotNone(alert)
        self.assertLessEqual(len(alert.message), 255)

    # --- badge query --------------------------------------------------------

    def test_unread_badge_count(self):
        # Mirrors the frontend's bell badge: count is_read == false.
        self._clean()
        self._notification(self.student, is_read=False)
        self._notification(self.student, is_read=False)
        self._notification(self.student, is_read=True)

        unread = Notification.objects.filter(
            recipient=self.student,
            is_read=False,
        ).count()
        self.assertEqual(unread, 2)

