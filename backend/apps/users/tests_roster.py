"""Roster import, account claim, assisted reset and rep promotion.

Launch-critical flows added on top of self-registration: admin CSV import
-> inert accounts (no password) -> student claims their own account;
assisted one-time reset (Option A — no email dependency); admin-only rep
promotion. Registered here under the users app, which owns all four.
"""
from datetime import timedelta

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from apps.users.models import ClaimBatch, Department, PasswordResetCode, User

VALID = 'S7rong!Passw0rd'


class RosterFlowTests(APITestCase):
    def setUp(self):
        # The auth throttle budget lives in the shared cache; clear it so
        # every test starts fresh (same pattern as the login throttle test).
        cache.clear()
        self.dept = Department.objects.create(
            name='Computer Science', faculty='Physical Sciences')
        self.other_dept = Department.objects.create(
            name='Law', faculty='Law')
        self.admin = User.objects.create_user(
            username='admin1', email='admin@school.edu.ng', password=VALID,
            matric_number='ADM/0000/001', department=self.dept, level='500',
            role=User.ROLE_ADMIN)
        self.rep = User.objects.create_user(
            username='rep1', email='rep@school.edu.ng', password=VALID,
            matric_number='CSC/2021/000', department=self.dept, level='400',
            role=User.ROLE_CLASS_REP)
        self.student = User.objects.create_user(
            username='stud1', email='stud@school.edu.ng', password=VALID,
            matric_number='CSC/2021/001', department=self.dept, level='400')

    # --- helpers -----------------------------------------------------------

    def _auth(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def _import(self, rows, **extra):
        headers = extra.pop(
            'headers', 'first_name,last_name,matric_number,level,department')
        body = headers + '\n' + '\n'.join(rows)
        data = {
            'file': SimpleUploadedFile(
                'roster.csv', body.encode('utf-8'), content_type='text/csv'),
        }
        data.update(extra)
        return self.client.post('/api/auth/import/', data, format='multipart')

    def _claim(self, matric, first_name, batch_code, password=VALID):
        return self.client.post('/api/auth/claim/', {
            'matric_number': matric,
            'first_name': first_name,
            'batch_code': batch_code,
            'password': password,
        }, format='json')

    def _import_one_then_get_code(self, matric='CSC/2021/777',
                                  first_name='Ada', last_name='Obi'):
        row = f'{first_name},{last_name},{matric},400,Computer Science'
        response = self._import([row])
        return response.data['claim_batch_code']

    # --- CSV import (admin only) -------------------------------------------

    def test_import_requires_admin(self):
        self._auth(self.rep)
        response = self._import(['A,B,CSC/2021/500,400,Computer Science'])
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_import_unauthenticated_is_401(self):
        response = self._import(['A,B,CSC/2021/500,400,Computer Science'])
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_import_without_file_is_400(self):
        self._auth(self.admin)
        response = self.client.post('/api/auth/import/', {}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_import_missing_required_column_is_400(self):
        self._auth(self.admin)
        response = self._import(
            ['CSC/2021/500,400'], headers='matric_number,level')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error'], 'bad_request')

    def test_import_dry_run_writes_nothing(self):
        self._auth(self.admin)
        response = self._import(
            ['Ada,Obi,CSC/2021/777,400,Computer Science'], dry_run='true')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['would_create'], 1)
        # Nothing was written, so no code is issued either.
        self.assertFalse(User.objects.filter(matric_number='CSC/2021/777').exists())
        self.assertNotIn('claim_batch_code', response.data)

    def test_import_creates_inert_account_and_issues_one_batch_code(self):
        self._auth(self.admin)
        response = self._import([
            'Ada,Obi,CSC/2021/777,400,Computer Science',
            'John,Doe,CSC/2021/778,400,Computer Science',
        ])
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['created'], 2)
        self.assertEqual(response.data['errors_total'], 0)
        self.assertTrue(response.data['claim_batch_code'])

        # One shared code for the whole file, not one per student.
        self.assertEqual(ClaimBatch.objects.count(), 1)

        imported = User.objects.get(matric_number='CSC/2021/777')
        self.assertEqual(imported.role, User.ROLE_STUDENT)
        self.assertEqual(imported.level, '400')
        self.assertEqual(imported.department_id, self.dept.id)
        # No email on file -> NULL, never '' ('' would collide on the unique
        # email index as soon as a second email-less student is imported).
        self.assertIsNone(imported.email)
        # Inert: no password exists yet, so it cannot be logged into.
        self.assertFalse(imported.has_usable_password())

    def test_imported_account_cannot_log_in_before_claiming(self):
        self._auth(self.admin)
        self._import(['Ada,Obi,CSC/2021/777,400,Computer Science'])
        self.client.credentials()  # drop the admin token
        response = self.client.post('/api/auth/login/', {
            'username': 'CSC/2021/777', 'password': VALID}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_import_skips_existing_matric_without_duplicating(self):
        self._auth(self.admin)
        response = self._import(
            ['Stud,One,CSC/2021/001,400,Computer Science'])  # already exists
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['created'], 0)
        self.assertEqual(response.data['skipped_existing'], 1)
        self.assertEqual(response.data['errors_total'], 1)
        self.assertEqual(
            User.objects.filter(matric_number='CSC/2021/001').count(), 1)

    def test_import_rejects_unknown_department_and_bad_level(self):
        self._auth(self.admin)
        response = self._import(['Ada,Obi,CSC/2021/777,999,Nonsense Studies'])
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['created'], 0)
        self.assertEqual(response.data['errors_total'], 1)
        row_errors = ' '.join(response.data['errors'][0]['errors'])
        self.assertIn('invalid level', row_errors)
        self.assertIn('unknown department', row_errors)

    def test_import_accepts_name_and_matric_only(self):
        # Owner decision: only name + matric are guaranteed to be on file.
        self._auth(self.admin)
        response = self._import(
            ['Ada,Obi,CSC/2021/777'],
            headers='first_name,last_name,matric_number')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['created'], 1)
        imported = User.objects.get(matric_number='CSC/2021/777')
        self.assertIsNone(imported.department_id)
        self.assertIsNone(imported.level)

    # --- claim flow --------------------------------------------------------

    def test_claim_sets_password_and_allows_login(self):
        self._auth(self.admin)
        code = self._import_one_then_get_code()
        self.client.credentials()  # claiming is public

        response = self._claim('CSC/2021/777', 'Ada', code)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        claimed = User.objects.get(matric_number='CSC/2021/777')
        self.assertTrue(claimed.has_usable_password())
        self.assertTrue(claimed.check_password(VALID))

        login = self.client.post('/api/auth/login/', {
            'username': 'CSC/2021/777', 'password': VALID}, format='json')
        self.assertEqual(login.status_code, status.HTTP_200_OK)

    def test_claim_failures_return_identical_generic_error(self):
        # Anti-enumeration: a wrong name, an unknown matric and a bad code
        # must be indistinguishable to the caller.
        self._auth(self.admin)
        code = self._import_one_then_get_code()
        self.client.credentials()

        wrong_name = self._claim('CSC/2021/777', 'NotAda', code)
        unknown_matric = self._claim('CSC/2099/999', 'Ada', code)
        bad_code = self._claim('CSC/2021/777', 'Ada', 'NOSUCHCODE')

        for response in (wrong_name, unknown_matric, bad_code):
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(response.data['error'], 'claim_failed')
        self.assertEqual(wrong_name.data, unknown_matric.data)
        self.assertEqual(wrong_name.data, bad_code.data)

    def test_claim_rejected_once_account_has_a_password(self):
        self._auth(self.admin)
        code = self._import_one_then_get_code()
        self.client.credentials()
        self.assertEqual(
            self._claim('CSC/2021/777', 'Ada', code).status_code,
            status.HTTP_200_OK)

        # A second claim on the same (now claimed) account must not work —
        # otherwise anyone with the batch code could seize an active account.
        replay = self._claim('CSC/2021/777', 'Ada', code, password='Other!Pass123')
        self.assertEqual(replay.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(replay.data['error'], 'claim_failed')
        self.assertTrue(
            User.objects.get(matric_number='CSC/2021/777').check_password(VALID))

    def test_claim_with_deactivated_batch_code_fails(self):
        self._auth(self.admin)
        code = self._import_one_then_get_code()
        batch = ClaimBatch.objects.get(code=code)
        self.client.post(f'/api/auth/claim-batches/{batch.id}/deactivate/')
        self.client.credentials()

        response = self._claim('CSC/2021/777', 'Ada', code)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error'], 'claim_failed')

    def test_claim_rejects_weak_password(self):
        self._auth(self.admin)
        code = self._import_one_then_get_code()
        self.client.credentials()

        response = self._claim('CSC/2021/777', 'Ada', code, password='123')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error'], 'weak_password')
        self.assertFalse(
            User.objects.get(matric_number='CSC/2021/777').has_usable_password())

    def test_claim_can_store_an_email_the_roster_lacked(self):
        self._auth(self.admin)
        code = self._import_one_then_get_code()
        self.client.credentials()

        response = self.client.post('/api/auth/claim/', {
            'matric_number': 'CSC/2021/777', 'first_name': 'Ada',
            'batch_code': code, 'password': VALID,
            'email': 'ada@school.edu.ng',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            User.objects.get(matric_number='CSC/2021/777').email,
            'ada@school.edu.ng')

    # --- assisted reset (Option A: rep issues code, student sets password) --

    def _issue_reset_code(self, matric):
        return self.client.post(
            '/api/auth/reset-code/', {'matric_number': matric}, format='json')

    def test_reset_code_requires_rep_or_admin(self):
        self._auth(self.student)
        response = self._issue_reset_code('CSC/2021/001')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_reset_code_unknown_matric_is_404(self):
        self._auth(self.admin)
        response = self._issue_reset_code('CSC/2099/999')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_rep_cannot_issue_code_for_another_department(self):
        other = User.objects.create_user(
            username='law1', email='law@school.edu.ng', password=VALID,
            matric_number='LAW/2021/001', department=self.other_dept, level='400')
        self._auth(self.rep)
        response = self._issue_reset_code(other.matric_number)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_reset_code_rejected_for_unclaimed_account(self):
        # An inert (never claimed) account must go through the claim flow —
        # a reset code must not become a back door around it.
        self._auth(self.admin)
        self._import_one_then_get_code()
        response = self._issue_reset_code('CSC/2021/777')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reset_code_is_single_use_and_replaces_older_codes(self):
        self._auth(self.admin)
        first = self._issue_reset_code('CSC/2021/001')
        second = self._issue_reset_code('CSC/2021/001')
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertNotEqual(first.data['code'], second.data['code'])

        # Only one code stays live for a student.
        live = PasswordResetCode.objects.filter(
            user=self.student, used_at__isnull=True)
        self.assertEqual(live.count(), 1)
        self.assertEqual(live.first().code, second.data['code'])

    def test_reset_password_with_valid_code_changes_password(self):
        self._auth(self.admin)
        code = self._issue_reset_code('CSC/2021/001').data['code']
        self.client.credentials()  # resetting is public + throttled

        response = self.client.post('/api/auth/reset-password/', {
            'matric_number': 'CSC/2021/001',
            'code': code,
            'new_password': 'Br@ndNewPass99',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.student.refresh_from_db()
        self.assertTrue(self.student.check_password('Br@ndNewPass99'))

    def test_reset_code_cannot_be_replayed(self):
        self._auth(self.admin)
        code = self._issue_reset_code('CSC/2021/001').data['code']
        self.client.credentials()
        self.client.post('/api/auth/reset-password/', {
            'matric_number': 'CSC/2021/001', 'code': code,
            'new_password': 'Br@ndNewPass99'}, format='json')

        replay = self.client.post('/api/auth/reset-password/', {
            'matric_number': 'CSC/2021/001', 'code': code,
            'new_password': 'Another!Pass77'}, format='json')
        self.assertEqual(replay.status_code, status.HTTP_400_BAD_REQUEST)
        self.student.refresh_from_db()
        self.assertTrue(self.student.check_password('Br@ndNewPass99'))

    def test_reset_password_rejects_expired_code(self):
        self._auth(self.admin)
        code = self._issue_reset_code('CSC/2021/001').data['code']
        self.client.credentials()
        # Age the code past its 30-minute TTL.
        PasswordResetCode.objects.filter(code=code).update(
            created_at=timezone.now() - timedelta(minutes=31))

        response = self.client.post('/api/auth/reset-password/', {
            'matric_number': 'CSC/2021/001', 'code': code,
            'new_password': 'Br@ndNewPass99'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error'], 'code_expired')

    def test_reset_password_rejects_code_for_a_different_matric(self):
        self._auth(self.admin)
        code = self._issue_reset_code('CSC/2021/001').data['code']
        self.client.credentials()
        response = self.client.post('/api/auth/reset-password/', {
            'matric_number': 'CSC/2021/000', 'code': code,
            'new_password': 'Br@ndNewPass99'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error'], 'invalid_code')

    # --- rep promotion (admin only) ----------------------------------------

    def test_admin_can_promote_student_to_class_rep(self):
        self._auth(self.admin)
        response = self.client.post(
            f'/api/auth/users/{self.student.id}/set-role/',
            {'role': User.ROLE_CLASS_REP}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.student.refresh_from_db()
        self.assertEqual(self.student.role, User.ROLE_CLASS_REP)

    def test_set_role_requires_admin(self):
        self._auth(self.rep)
        response = self.client.post(
            f'/api/auth/users/{self.student.id}/set-role/',
            {'role': User.ROLE_CLASS_REP}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.student.refresh_from_db()
        self.assertEqual(self.student.role, User.ROLE_STUDENT)

    def test_set_role_cannot_change_own_role(self):
        self._auth(self.admin)
        response = self.client.post(
            f'/api/auth/users/{self.admin.id}/set-role/',
            {'role': User.ROLE_STUDENT}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_set_role_rejects_privileged_target_role(self):
        # The rep-promotion endpoint must never be a route to admin.
        self._auth(self.admin)
        response = self.client.post(
            f'/api/auth/users/{self.student.id}/set-role/',
            {'role': User.ROLE_ADMIN}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.student.refresh_from_db()
        self.assertEqual(self.student.role, User.ROLE_STUDENT)

