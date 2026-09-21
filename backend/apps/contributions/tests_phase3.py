"""Phase 3 tests: editing and closing a contribution."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from apps.contributions.models import Contribution
from apps.users.models import Department, User


class ContributionEditAndCloseTests(APITestCase):
    def setUp(self):
        self.department = Department.objects.create(
            name='Computer Science', faculty='Physical Sciences'
        )
        self.other_department = Department.objects.create(
            name='Physics', faculty='Physical Sciences'
        )
        self.rep = User.objects.create_user(
            username='rep1', email='rep1@example.com',
            password='S7rong!Passw0rd', matric_number='CSC/2021/003',
            department=self.department, level='500',
            role=User.ROLE_CLASS_REP,
        )
        self.student = User.objects.create_user(
            username='student1', email='student1@example.com',
            password='S7rong!Passw0rd', matric_number='CSC/2021/041',
            department=self.department, level='400',
        )
        self.outsider_rep = User.objects.create_user(
            username='rep2', email='rep2@example.com',
            password='S7rong!Passw0rd', matric_number='PHY/2021/003',
            department=self.other_department, level='500',
            role=User.ROLE_CLASS_REP,
        )
        self.contribution = Contribution.objects.create(
            department=self.department, created_by=self.rep,
            title='Excursion Fee', description='Trip',
            amount=Decimal('5000.00'),
            deadline=timezone.now() + timedelta(days=30),
            is_mandatory=True,
        )

    def _auth(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def _url(self):
        return f'/api/contributions/{self.contribution.id}/'

    # --- PATCH: editing a fee -------------------------------------------

    def test_rep_can_patch_a_fee(self):
        self._auth(self.rep)
        response = self.client.patch(
            self._url(),
            {'title': 'Excursion Fee (revised)', 'amount': '6500.00'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.title, 'Excursion Fee (revised)')
        # Money stays an exact Decimal, rendered as a string.
        self.assertEqual(self.contribution.amount, Decimal('6500.00'))
        self.assertEqual(response.data['amount'], '6500.00')

    def test_student_cannot_patch_a_fee(self):
        self._auth(self.student)
        response = self.client.patch(
            self._url(), {'title': 'Hacked'}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.title, 'Excursion Fee')

    def test_rep_cannot_patch_another_departments_fee(self):
        # Out of department scope => 404 (existence is not leaked).
        self._auth(self.outsider_rep)
        response = self.client.patch(
            self._url(), {'title': 'Hijacked'}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.title, 'Excursion Fee')

    def test_rep_cannot_move_a_fee_to_another_department(self):
        # Moving a collection to a department you don't own would hand off (or
        # hijack) money owed by students who never agreed to it.
        self._auth(self.rep)
        response = self.client.patch(
            self._url(),
            {'department_id': self.other_department.id},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.department, self.department)

    def test_put_is_not_allowed(self):
        # Cheap guard: a half-specified PUT could blank a required field.
        self._auth(self.rep)
        response = self.client.put(self._url(), {'title': 'x'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    # --- DELETE: closing a fee ------------------------------------------

    def test_delete_closes_the_fee_and_keeps_the_row(self):
        self._auth(self.rep)
        response = self.client.delete(self._url())

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        # The row MUST survive — it is the audit trail for money collected.
        self.contribution.refresh_from_db()
        self.assertTrue(self.contribution.is_closed)

    def test_student_cannot_close_a_fee(self):
        self._auth(self.student)
        response = self.client.delete(self._url())

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.contribution.refresh_from_db()
        self.assertFalse(self.contribution.is_closed)

    def test_closed_fee_is_hidden_from_students(self):
        self.contribution.is_closed = True
        self.contribution.save(update_fields=['is_closed'])

        self._auth(self.student)
        response = self.client.get('/api/contributions/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_closed_fee_stays_visible_to_its_rep(self):
        # A rep must still be able to see and reopen what they closed, and to
        # report on what it collected.
        self.contribution.is_closed = True
        self.contribution.save(update_fields=['is_closed'])

        self._auth(self.rep)
        response = self.client.get('/api/contributions/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row['id'] for row in response.data],
                         [self.contribution.id])
        self.assertIs(response.data[0]['is_closed'], True)

    def test_closed_fee_summary_still_reports_its_totals(self):
        self.contribution.is_closed = True
        self.contribution.save(update_fields=['is_closed'])

        self._auth(self.rep)
        response = self.client.get(f'{self._url()}summary/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # 2 eligible students (the student + the class rep, who still pays dues).
        self.assertEqual(response.data['total_expected'], '10000.00')
        self.assertEqual(response.data['outstanding_count'], 2)

    def test_closed_fee_cannot_be_paid(self):
        self.contribution.is_closed = True
        self.contribution.save(update_fields=['is_closed'])

        self._auth(self.student)
        response = self.client.post(
            '/api/payments/initiate/',
            {'contribution_id': self.contribution.id},
            format='json',
        )

        # Same answer as an expired fee: not found, so a stale id can't start a
        # payment for a collection nobody is accepting.
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data['error'], 'not_found')

    def test_reopening_a_closed_fee_makes_it_payable_again(self):
        self.contribution.is_closed = True
        self.contribution.save(update_fields=['is_closed'])

        self._auth(self.rep)
        response = self.client.patch(
            self._url(), {'is_closed': False}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.contribution.refresh_from_db()
        self.assertFalse(self.contribution.is_closed)

        self._auth(self.student)
        listed = self.client.get('/api/contributions/')
        self.assertEqual([row['id'] for row in listed.data],
                         [self.contribution.id])
