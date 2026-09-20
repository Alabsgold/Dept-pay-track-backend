from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from apps.payments.models import Payment
from apps.users.models import Department, User

from .models import Contribution


class ContributionTests(APITestCase):
    """Every test asserts exact Decimal values — no approximate floats."""

    def setUp(self):
        self.dept = Department.objects.create(name='Computer Science', faculty='Physical Sciences')
        self.other_dept = Department.objects.create(name='Law', faculty='Law')
        self.student = self._u('student', 'student@school.edu.ng', 'CSC/2021/001', self.dept, '400')
        self.level100 = self._u('level100', 'level100@school.edu.ng', 'CSC/2021/002', self.dept, '100')
        self.other_student = self._u('lawstudent', 'law@school.edu.ng', 'LAW/2021/001', self.other_dept, '100')
        self.rep = self._u('rep', 'rep@school.edu.ng', 'CSC/2021/003', self.dept, '500', User.ROLE_CLASS_REP)
        self.admin = self._u('admin', 'admin@school.edu.ng', 'CSC/2021/004', self.dept, '500', User.ROLE_ADMIN)
        self.rep_no_dept = self._u('repnoded', 'repnodept@school.edu.ng', 'CSC/2021/005', None, '500', User.ROLE_CLASS_REP)
        self.contribution = self._c('Departmental Shirt 2026', '3500.00', 30, None)
        self.expired = self._c('Expired Dues', '1000.00', -1, None)
        self.level100_only = self._c('Level 100 Orientation', '2000.00', 10, '100')

    # --- helpers ---

    def _u(self, username, email, matric, dept, level, role=User.ROLE_STUDENT):
        return User.objects.create_user(
            username=username, email=email, password='S7rong!Passw0rd',
            matric_number=matric, department=dept, level=level, role=role)

    def _c(self, title, amount, deadline_days, target_level):
        return Contribution.objects.create(
            department=self.dept, created_by=self.rep, title=title,
            description='desc', amount=Decimal(amount),
            deadline=timezone.now() + timedelta(days=deadline_days),
            is_mandatory=True, target_level=target_level)

    def _auth(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def _payload(self):
        return {
            'title': 'Excursion Fee', 'description': 'End of year excursion',
            'amount': '5000.00', 'deadline': '2026-10-15T23:59:00Z',
            'is_mandatory': True, 'target_level': '400',
        }

    def _post_create(self, user, payload=None):
        self._auth(user)
        return self.client.post(
            reverse('contribution-list-create'), payload or self._payload(), format='json')

    # --- auth guards ---

    def test_roster_shows_success_even_when_an_older_attempt_failed(self):
        # The roster must never report a paid student as unpaid: a stale failed
        # attempt must not mask the successful one (the pre-fix map let the
        # OLDEST row win, because rows are newest-first).
        Payment.objects.create(
            student=self.student, contribution=self.contribution,
            payment_type='contribution', amount=Decimal('3500.00'),
            reference='ROSTER-OLD-FAILED', status='failed',
            method='online')
        Payment.objects.create(
            student=self.student, contribution=self.contribution,
            payment_type='contribution', amount=Decimal('3500.00'),
            reference='ROSTER-NEW-SUCCESS', status='success',
            method='online')

        self._auth(self.rep)
        r = self.client.get(
            reverse('contribution-payments', args=[self.contribution.pk]))

        self.assertEqual(r.status_code, 200)
        row = next(x for x in r.data if x['matric_number'] == 'CSC/2021/001')
        self.assertEqual(row['status'], 'success')
        self.assertIsNotNone(row['paid_at'])
        # ...and the money is counted once, not twice.
        self.assertEqual(self.contribution.total_collected(), Decimal('3500.00'))

    def test_mark_paid_rejects_student_outside_the_target_level(self):
        # A level-targeted fee must not be markable for the wrong level; the
        # bogus success row would inflate collected totals.
        self._auth(self.rep)
        r = self.client.post(
            reverse('contribution-payments', args=[self.level100_only.pk]),
            {'matric_number': self.student.matric_number},  # level 400
            format='json')

        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data['error'], 'bad_request')
        self.assertFalse(self.level100_only.has_paid(self.student))

    def test_mark_paid_accepts_student_in_the_target_level(self):
        self._auth(self.rep)
        r = self.client.post(
            reverse('contribution-payments', args=[self.level100_only.pk]),
            {
                'matric_number': self.level100.matric_number,  # level 100
                'receipt_reference': 'RCPT-L100-001',
            },
            format='json')

        self.assertEqual(r.status_code, 201)
        self.assertTrue(self.level100_only.has_paid(self.level100))

    def test_unauthenticated_list_is_401(self):
        self.assertEqual(self.client.get(reverse('contribution-list-create')).status_code, 401)

    def test_unauthenticated_summary_is_401(self):
        r = self.client.get(reverse('contribution-summary', args=[self.contribution.pk]))
        self.assertEqual(r.status_code, 401)

    def test_unauthenticated_payments_list_is_401(self):
        r = self.client.get(reverse('contribution-payments', args=[self.contribution.pk]))
        self.assertEqual(r.status_code, 401)

    # --- listing / scoping ---

    def test_student_lists_only_own_department_active_contributions(self):
        self._auth(self.student)
        r = self.client.get(reverse('contribution-list-create'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([row['title'] for row in r.data], ['Departmental Shirt 2026'])

    def test_student_list_respects_target_level(self):
        self._auth(self.level100)
        r = self.client.get(reverse('contribution-list-create'))
        self.assertEqual(set(row['title'] for row in r.data),
                         {'Level 100 Orientation', 'Departmental Shirt 2026'})

    def test_student_list_excludes_expired(self):
        self._auth(self.student)
        r = self.client.get(reverse('contribution-list-create'))
        self.assertNotIn('Expired Dues', [row['title'] for row in r.data])

    def test_student_list_shape_matches_contract(self):
        self._auth(self.student)
        r = self.client.get(reverse('contribution-list-create'))
        row = r.data[0]
        self.assertEqual(
            sorted(row.keys()),
            sorted(['id', 'title', 'amount', 'deadline', 'is_mandatory', 'target_level', 'has_paid']))
        self.assertIs(row['has_paid'], False)
        self.assertEqual(row['amount'], '3500.00')  # string, decimal, exact

    def test_other_department_student_sees_nothing(self):
        self._auth(self.other_student)
        r = self.client.get(reverse('contribution-list-create'))
        self.assertEqual(r.data, [])

    # --- creation / authorization ---

    def test_student_cannot_create(self):
        self.assertEqual(self._post_create(self.student).status_code, 403)

    def test_class_rep_can_create(self):
        r = self._post_create(self.rep)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(
            sorted(r.data.keys()),
            sorted(['id', 'title', 'amount', 'deadline', 'is_mandatory',
                    'target_level', 'has_paid', 'department_id']))
        self.assertEqual(r.data['amount'], '5000.00')
        self.assertIs(r.data['has_paid'], False)
        c = Contribution.objects.get(pk=r.data['id'])
        self.assertEqual(c.department, self.dept)
        self.assertEqual(c.created_by, self.rep)
        self.assertEqual(c.amount, Decimal('5000.00'))
        self.assertIsInstance(c.amount, Decimal)

    def test_admin_can_create(self):
        self.assertEqual(self._post_create(self.admin).status_code, 201)

    def test_class_rep_without_department_cannot_create(self):
        self.assertEqual(self._post_create(self.rep_no_dept).status_code, 400)

    def test_client_cannot_set_department_or_creator(self):
        self._auth(self.rep)
        payload = self._payload()
        payload['department_id'] = self.other_dept.pk  # injection attempt
        r = self.client.post(reverse('contribution-list-create'), payload, format='json')
        self.assertIn(r.status_code, (400, 201))
        if r.status_code == 201:
            saved = Contribution.objects.get(pk=r.data['id'])
            self.assertEqual(saved.department, self.dept)  # server wins

    # --- validation ---

    def test_zero_amount_rejected(self):
        payload = self._payload()
        payload['amount'] = '0'
        r = self._post_create(self.rep, payload)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data['error'], 'bad_request')

    def test_negative_amount_rejected(self):
        payload = self._payload()
        payload['amount'] = '-5.00'
        self.assertEqual(self._post_create(self.rep, payload).status_code, 400)

    def test_invalid_target_level_rejected(self):
        payload = self._payload()
        payload['target_level'] = '999'
        self.assertEqual(self._post_create(self.rep, payload).status_code, 400)

    def test_invalid_deadline_rejected(self):
        payload = self._payload()
        payload['deadline'] = 'not-a-date'
        self.assertEqual(self._post_create(self.rep, payload).status_code, 400)

    def test_missing_title_rejected(self):
        payload = self._payload()
        payload.pop('title')
        self.assertEqual(self._post_create(self.rep, payload).status_code, 400)

    # --- detail / IDOR ---

    def test_detail_visible_for_own_department(self):
        self._auth(self.student)
        r = self.client.get(reverse('contribution-detail', args=[self.contribution.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['title'], 'Departmental Shirt 2026')

    def test_detail_hidden_for_other_department(self):
        self._auth(self.other_student)
        r = self.client.get(reverse('contribution-detail', args=[self.contribution.pk]))
        self.assertEqual(r.status_code, 404)

    # --- summary ---

    def test_summary_totals_are_exact_decimals(self):
        # Eligible (target_level null): student, level100, rep = 3 (the
        # admin is exempt — only students/reps pay dues).
        self._auth(self.rep)
        r = self.client.get(reverse('contribution-summary', args=[self.contribution.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['total_expected'], '10500.00')
        self.assertEqual(r.data['total_collected'], '0.00')
        self.assertEqual(r.data['outstanding_count'], 3)

    def test_summary_scoped_to_department(self):
        self._auth(self.other_student)
        r = self.client.get(reverse('contribution-summary', args=[self.contribution.pk]))
        self.assertEqual(r.status_code, 404)

    # --- payments status list (class rep/admin only) ---

    def test_payments_status_rejects_student(self):
        token, _ = Token.objects.get_or_create(user=self.student)
        r = self.client.get(
            reverse('contribution-payments', args=[self.contribution.pk]),
            HTTP_AUTHORIZATION='Token ' + token.key)
        self.assertEqual(r.status_code, 403)

    def test_payments_status_accepts_rep(self):
        self._auth(self.rep)
        r = self.client.get(reverse('contribution-payments', args=[self.contribution.pk]))
        self.assertEqual(r.status_code, 200)
        # 3 rows: student, level100, rep — the admin is NOT owed dues.
        self.assertEqual(len(r.data), 3)
        for row in r.data:
            self.assertEqual(sorted(row.keys()),
                             sorted(['student', 'matric_number', 'status', 'paid_at']))

    def test_payments_status_accepts_admin(self):
        self._auth(self.admin)
        r = self.client.get(reverse('contribution-payments', args=[self.contribution.pk]))
        self.assertEqual(r.status_code, 200)

    # --- mark as paid (offline) ---

    def test_student_cannot_mark_paid(self):
        token, _ = Token.objects.get_or_create(user=self.student)
        r = self.client.post(
            reverse('contribution-payments', args=[self.contribution.pk]),
            {'matric_number': self.student.matric_number},
            format='json',
            HTTP_AUTHORIZATION='Token ' + token.key)
        self.assertEqual(r.status_code, 403)

    def test_rep_marks_student_paid(self):
        self._auth(self.rep)
        r = self.client.post(
            reverse('contribution-payments', args=[self.contribution.pk]),
            {'matric_number': self.student.matric_number, 'receipt_reference': 'RCPT-001'},
            format='json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data['status'], 'success')
        self.assertEqual(r.data['method'], 'manual')
        self.assertEqual(r.data['matric_number'], 'CSC/2021/001')

        # Downstream logic now lights up: has_paid is True, summary reflects it.
        self.assertTrue(self.contribution.has_paid(self.student))
        summary = self.client.get(
            reverse('contribution-summary', args=[self.contribution.pk]))
        self.assertEqual(summary.data['total_collected'], '3500.00')

    def test_rep_cannot_mark_self(self):
        self._auth(self.rep)
        r = self.client.post(
            reverse('contribution-payments', args=[self.contribution.pk]),
            {'matric_number': self.rep.matric_number},
            format='json')
        self.assertEqual(r.status_code, 403)

    def test_admin_can_mark_self(self):
        self._auth(self.admin)
        r = self.client.post(
            reverse('contribution-payments', args=[self.contribution.pk]),
            {'matric_number': self.admin.matric_number, 'receipt_reference': 'RCPT-ADMIN-001'},
            format='json')
        self.assertEqual(r.status_code, 201)

    def test_duplicate_mark_returns_409(self):
        self._auth(self.rep)
        url = reverse('contribution-payments', args=[self.contribution.pk])
        self.client.post(url, {'matric_number': self.student.matric_number,
                               'receipt_reference': 'RCPT-DUP-001'}, format='json')
        r = self.client.post(url, {'matric_number': self.student.matric_number,
                                   'receipt_reference': 'RCPT-DUP-002'}, format='json')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data['error'], 'already_paid')

    def test_mark_student_from_other_department_rejected(self):
        self._auth(self.rep)
        r = self.client.post(
            reverse('contribution-payments', args=[self.contribution.pk]),
            {'matric_number': self.other_student.matric_number},
            format='json')
        self.assertEqual(r.status_code, 400)

    def test_mark_nonexistent_student_404(self):
        self._auth(self.rep)
        r = self.client.post(
            reverse('contribution-payments', args=[self.contribution.pk]),
            {'matric_number': 'NOPE/0000/999'},
            format='json')
        self.assertEqual(r.status_code, 404)

    def test_mark_missing_matric_400(self):
        self._auth(self.rep)
        r = self.client.post(
            reverse('contribution-payments', args=[self.contribution.pk]),
            {},
            format='json')
        self.assertEqual(r.status_code, 400)

    def test_mark_without_receipt_reference_400(self):
        # Offline marks must be auditable: no receipt number, no mark.
        self._auth(self.rep)
        r = self.client.post(
            reverse('contribution-payments', args=[self.contribution.pk]),
            {'matric_number': self.student.matric_number},
            format='json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data['error'], 'bad_request')
        self.assertFalse(self.contribution.has_paid(self.student))

    def test_cross_department_rep_cannot_mark(self):
        # A rep from another department can't even see the contribution (404).
        rep_other = self._u('repother', 'repother@school.edu.ng', 'LAW/2021/002',
                            self.other_dept, '500', User.ROLE_CLASS_REP)
        token, _ = Token.objects.get_or_create(user=rep_other)
        r = self.client.post(
            reverse('contribution-payments', args=[self.contribution.pk]),
            {'matric_number': self.student.matric_number},
            format='json',
            HTTP_AUTHORIZATION='Token ' + token.key)
        self.assertEqual(r.status_code, 404)