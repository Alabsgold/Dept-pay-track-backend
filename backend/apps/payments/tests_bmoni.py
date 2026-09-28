"""
BMONI Embedded tests — a department's own NGN virtual account.

Nothing in this module touches the network: `requests.request` is faked, so the
real client and provisioning code run while CI needs no BMONI key. The opt-in
checks that DO hit the sandbox live in `tests_bmoni_live.py`.

The fake responses are copied from real sandbox payloads (see
docs/BMONI_SANDBOX_RUNBOOK.md), including the trap this suite exists to pin
down: a deposit-accounts response contains a pooled provider account
(`activationAccounts` → `Bkey Limited`) that must never be shown as the
department's own.
"""
import hashlib
import hmac
import json
from unittest.mock import patch

import requests
from django.db import IntegrityError, transaction
from django.test import override_settings
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from apps.users.models import Department, User

from .bmoni_client import (
    BMONIClient,
    BMONIError,
    BMONINotConfigured,
    BMONIUnavailable,
    REQUEST_TIMEOUT,
    provision_ngn_account,
    sandbox_wallet_address,
)
from .models import BMONIWebhookEvent, DepartmentBMONIWallet

# --- real sandbox payloads (Dillon Bunch persona, 2026-09-26) ---------------

DEPOSIT_ACCOUNTS = {
    'nigerianAccounts': [
        {
            'id': '4c9342a0-1289-4b6e-a9b7-7af73c7469be',
            'accountName': 'Dillon Bunch',
            'bankName': 'PROVIDUS BANK',
            'accountNumber': '9845221370',
            'bankCode': '000023',
            'currency': 'NGN',
            'targetCurrency': 'NGN',
        }
    ],
    'activationAccounts': [
        {
            'id': 'pooled-vba-1',
            'accountName': 'Bkey Limited',
            'bankName': '9 Payment Service Bank',
            'accountNumber': '6177463833',
            'bankCode': 'XXXXXXX',
            'currency': 'NGN',
            'targetCurrency': 'EUR',
        }
    ],
    'europeanAccounts': [],
    'usaAccounts': [],
    'mexicanAccounts': [],
}

CREATED_USER = {'user': {'bmoniUserId': 'ec6c5a47-70af-43f5-9559-b5230b8749d9'}}

SANDBOX_SETTINGS = {
    'BMONI_API_KEY': 'test-bmoni-partner-key',
    'BMONI_BASE_URL': 'https://embedded-dev.bmoni.com',
    'BMONI_WEBHOOK_SECRET': 'test-webhook-secret',
}


def _query_int(path, key, default):
    """A query parameter as an int (the lookups paginate with ?page=&limit=)."""
    for part in path.partition('?')[2].split('&'):
        name, _, value = part.partition('=')
        if name == key and value.isdigit():
            return int(value)
    return default


class FakeResponse:
    """The only part of a `requests` response the client looks at."""

    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError('not json')
        return self._payload


class FakeBMONI:
    """
    BMONI's HTTP surface, faked per path.

    Patched in place of `requests.request` so the REAL client and provisioning
    flow run end to end — faking our own functions instead would prove nothing
    about the code that talks to BMONI. `calls` records what was asked for, which
    is how the idempotency tests prove NO second account was created.
    """

    def __init__(self, *, conflict=False, deposit_accounts=None, create=None,
                 existing_user=None, existing_user_page=1):
        self.conflict = conflict
        self.deposit_accounts = (
            DEPOSIT_ACCOUNTS if deposit_accounts is None else deposit_accounts
        )
        self.create = CREATED_USER if create is None else create
        # For the 409-recovery path: the person a duplicate create collided with,
        # and which page of the (paginated) list they sit on.
        self.existing_user = existing_user
        self.existing_user_page = existing_user_page
        self.calls = []

    @property
    def create_calls(self):
        return [call for call in self.calls if call[:2] == ('POST', 'users')]

    def __call__(self, method, url, **kwargs):
        path = url.split('/v1/', 1)[-1]
        body = kwargs.get('json')
        self.calls.append((method, path, body, kwargs))

        if method == 'POST' and path == 'users':
            if self.conflict:
                return FakeResponse(409, {
                    'statusCode': 409,
                    'message': 'User already exists',
                    'error': 'Conflict',
                })
            return FakeResponse(201, self.create)

        if method == 'PATCH' and path.endswith('/kyc'):
            return FakeResponse(200, {'status': 'ok'})

        if method == 'POST' and path.endswith('/onboarding/start-nigeria'):
            return FakeResponse(200, {
                'workflowId': 'onboarding-1',
                'isNigeria': True,
                'status': {'hasBvn': True, 'hasLocalWallet': True},
            })

        if method == 'GET' and path.endswith('/bank-accounts/deposit-accounts'):
            return FakeResponse(200, self.deposit_accounts)

        # The paginated list, matched last so it cannot swallow the paths above.
        if method == 'GET' and (path == 'users' or path.startswith('users?')):
            # Paginated exactly like BMONI: {users, total, page, limit}, and the
            # person may sit on a page after the first.
            page = _query_int(path, 'page', 1)
            limit = _query_int(path, 'limit', 20)
            if self.existing_user is not None:
                total = self.existing_user_page * limit
                if page == self.existing_user_page:
                    users = [self.existing_user]
                elif page < self.existing_user_page:
                    # A FULL page of other people, the way a real shared list
                    # looks when the person you want is further down.
                    users = [
                        {
                            'bmoniUserId': f'filler-{page}-{index}',
                            'email': f'filler{page}{index}@example.com',
                        }
                        for index in range(limit)
                    ]
                else:
                    users = []
            else:
                users = [{
                    'bmoniUserId': 'recovered-user-1',
                    'email': (self.create_calls[0][2] or {}).get('email'),
                }]
                total = 1
            return FakeResponse(200, {
                'users': users, 'total': total, 'page': page, 'limit': limit,
            })

        raise AssertionError(f'Unexpected BMONI call: {method} {path}')


@override_settings(**SANDBOX_SETTINGS)
class BMONITestBase(APITestCase):
    """Two departments with a rep/student/admin each, and the sandbox payloads."""

    def setUp(self):
        self.department = Department.objects.create(
            name='Computer Science', faculty='Physical Sciences'
        )
        self.other_department = Department.objects.create(
            name='Physics', faculty='Physical Sciences'
        )
        self.rep = User.objects.create_user(
            username='rep1',
            email='rep1@example.com',
            password='TestPassword123!',
            matric_number='TEST/2026/101',
            department=self.department,
            level='400',
            role=User.ROLE_CLASS_REP,
        )
        self.student = User.objects.create_user(
            username='student1',
            email='student1@example.com',
            password='TestPassword123!',
            matric_number='TEST/2026/102',
            department=self.department,
            level='400',
        )
        self.admin = User.objects.create_user(
            username='admin1',
            email='admin1@example.com',
            password='TestPassword123!',
            matric_number='TEST/2026/103',
            department=self.department,
            level='500',
            role=User.ROLE_ADMIN,
        )
        self.other_rep = User.objects.create_user(
            username='rep2',
            email='rep2@example.com',
            password='TestPassword123!',
            matric_number='TEST/2026/104',
            department=self.other_department,
            level='400',
            role=User.ROLE_CLASS_REP,
        )
        self.url = f'/api/payments/departments/{self.department.id}/bank-account/'

    def auth(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

    def payload(self, **overrides):
        body = {
            'first_name': 'Bunch',
            'last_name': 'Dillon',
            'email': 'holder@example.com',
            'phone_number': '+2348012345678',
            'bvn': '95888168924',
        }
        body.update(overrides)
        return body

    def run_with(self, fake):
        """Patch the HTTP layer with `fake` (a FakeBMONI)."""
        return patch('apps.payments.bmoni_client.requests.request', fake)


class DepartmentBankAccountReadTests(BMONITestBase):
    """GET — what a student sees on the payment screen."""

    def test_department_without_an_account_is_a_200_not_a_404(self):
        self.auth(self.student)

        response = self.client.get(self.url)

        # Not provisioned is a normal state the UI renders as "ask your rep",
        # so it must not look like an error.
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['provisioned'])
        self.assertIsNone(response.data['bank_account'])
        self.assertEqual(response.data['department']['id'], self.department.id)
        self.assertIn('message', response.data)

    def test_another_departments_account_is_a_404(self):
        wallet = DepartmentBMONIWallet.objects.create(
            department=self.other_department,
            account_name='Someone Else',
            account_number='1234567890',
            bank_name='PROVIDUS BANK',
            status=DepartmentBMONIWallet.STATUS_ACTIVE,
        )
        self.auth(self.student)

        response = self.client.get(
            f'/api/payments/departments/{self.other_department.id}/bank-account/'
        )

        # A department you may not see must be indistinguishable from one that
        # does not exist.
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertNotIn(wallet.account_number, str(response.data))

    def test_a_member_sees_the_issued_account(self):
        DepartmentBMONIWallet.objects.create(
            department=self.department,
            account_name='Dillon Bunch',
            account_number='9845221370',
            bank_name='PROVIDUS BANK',
            bank_code='000023',
            bmoni_user_id='ec6c5a47-70af-43f5-9559-b5230b8749d9',
            status=DepartmentBMONIWallet.STATUS_ACTIVE,
        )
        self.auth(self.student)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['provisioned'])
        self.assertEqual(response.data['bank_account']['account_number'], '9845221370')
        self.assertEqual(response.data['bank_account']['account_name'], 'Dillon Bunch')
        self.assertEqual(response.data['bank_account']['currency'], 'NGN')
        self.assertNotIn('message', response.data)

    def test_authentication_is_required(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_an_admin_may_read_any_department(self):
        self.auth(self.admin)

        response = self.client.get(
            f'/api/payments/departments/{self.other_department.id}/bank-account/'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['department']['id'], self.other_department.id)


class DepartmentBankAccountProvisionTests(BMONITestBase):
    """POST — issuing the department's own account through BMONI."""

    def test_a_student_may_not_open_an_account(self):
        fake = FakeBMONI()
        self.auth(self.student)

        with self.run_with(fake):
            response = self.client.post(self.url, self.payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(fake.calls, [])
        self.assertFalse(DepartmentBMONIWallet.objects.exists())

    def test_a_rep_provisions_the_departments_own_ngn_account(self):
        fake = FakeBMONI()
        self.auth(self.rep)

        with self.run_with(fake):
            response = self.client.post(self.url, self.payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['provisioned'])

        wallet = DepartmentBMONIWallet.objects.get(department=self.department)
        self.assertEqual(wallet.status, DepartmentBMONIWallet.STATUS_ACTIVE)
        self.assertEqual(wallet.account_number, '9845221370')
        self.assertEqual(wallet.account_name, 'Dillon Bunch')
        self.assertEqual(wallet.bank_name, 'PROVIDUS BANK')
        self.assertEqual(wallet.bank_code, '000023')
        self.assertEqual(wallet.currency, 'NGN')
        self.assertEqual(
            wallet.bmoni_user_id, 'ec6c5a47-70af-43f5-9559-b5230b8749d9'
        )
        self.assertIsNotNone(wallet.provisioned_at)

    def test_the_pooled_activation_account_is_never_used(self):
        """
        The trap: `activationAccounts` is `Bkey Limited`, a POOLED provider
        account. Showing it would send contributions to a name that is not the
        department's.
        """
        fake = FakeBMONI()
        self.auth(self.rep)

        with self.run_with(fake):
            response = self.client.post(self.url, self.payload(), format='json')

        wallet = DepartmentBMONIWallet.objects.get(department=self.department)
        self.assertNotEqual(wallet.account_name, 'Bkey Limited')
        self.assertNotEqual(wallet.account_number, '6177463833')
        self.assertNotIn('Bkey Limited', str(response.data))

    def test_only_the_last_four_bvn_digits_are_stored(self):
        fake = FakeBMONI()
        self.auth(self.rep)

        with self.run_with(fake):
            self.client.post(self.url, self.payload(), format='json')

        wallet = DepartmentBMONIWallet.objects.get(department=self.department)
        self.assertEqual(wallet.holder_bvn_last4, '8924')
        for value in vars(wallet).values():
            self.assertNotIn('95888168924', str(value))

    def test_pressing_provision_twice_never_creates_a_second_account(self):
        fake = FakeBMONI()
        self.auth(self.rep)

        with self.run_with(fake):
            first = self.client.post(self.url, self.payload(), format='json')
            second = self.client.post(self.url, self.payload(), format='json')

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        # Idempotent: the SAME account comes back and BMONI is not called again.
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data['bank_account'], first.data['bank_account'])
        self.assertEqual(len(fake.create_calls), 1)
        self.assertEqual(DepartmentBMONIWallet.objects.count(), 1)

    def test_a_rep_cannot_provision_another_department(self):
        fake = FakeBMONI()
        self.auth(self.rep)

        with self.run_with(fake):
            response = self.client.post(
                f'/api/payments/departments/{self.other_department.id}/bank-account/',
                self.payload(),
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(fake.calls, [])

    def test_missing_or_malformed_details_never_reach_bmoni(self):
        fake = FakeBMONI()
        self.auth(self.rep)

        for body in (
            self.payload(bvn=''),
            self.payload(bvn='12345'),
            self.payload(bvn='1234567890a'),
            self.payload(first_name=''),
            self.payload(email=''),
            self.payload(phone_number=''),
        ):
            with self.run_with(fake):
                response = self.client.post(self.url, body, format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertEqual(response.data['error'], 'bad_request')

        self.assertEqual(fake.calls, [])
        self.assertFalse(DepartmentBMONIWallet.objects.exists())

    def test_an_unreachable_gateway_is_a_502_and_the_wallet_is_failed(self):
        self.auth(self.rep)

        with patch(
            'apps.payments.bmoni_client.requests.request',
            side_effect=requests.ConnectionError('boom'),
        ):
            response = self.client.post(self.url, self.payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.data['error'], 'gateway_unavailable')
        wallet = DepartmentBMONIWallet.objects.get(department=self.department)
        self.assertEqual(wallet.status, DepartmentBMONIWallet.STATUS_FAILED)
        self.assertEqual(wallet.account_number, '')

    def test_bmoni_rejecting_the_details_is_a_400_without_leaking_the_bvn(self):
        self.auth(self.rep)

        def reject(method, url, **kwargs):
            return FakeResponse(400, {
                'statusCode': 400,
                'message': 'BVN 95888168924 could not be verified',
                'error': 'Bad Request',
            })

        with patch('apps.payments.bmoni_client.requests.request', reject):
            response = self.client.post(self.url, self.payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error'], 'bad_request')
        # The upstream message echoes our BVN: it must not come back in the
        # clear, and it must not be stored in the clear either.
        self.assertNotIn('95888168924', str(response.data))
        wallet = DepartmentBMONIWallet.objects.get(department=self.department)
        self.assertNotIn('95888168924', wallet.last_error)
        self.assertEqual(wallet.status, DepartmentBMONIWallet.STATUS_FAILED)

    def test_an_unconfigured_server_is_a_503_and_promises_nothing(self):
        self.auth(self.rep)

        with override_settings(BMONI_API_KEY=''):
            response = self.client.post(self.url, self.payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data['error'], 'unavailable')
        wallet = DepartmentBMONIWallet.objects.get(department=self.department)
        self.assertEqual(wallet.status, DepartmentBMONIWallet.STATUS_FAILED)

    def test_an_account_that_comes_back_without_a_number_is_never_shown(self):
        # Only the pooled bucket came back: that is NOT the department's account.
        fake = FakeBMONI(deposit_accounts={
            'nigerianAccounts': [],
            'activationAccounts': DEPOSIT_ACCOUNTS['activationAccounts'],
        })
        self.auth(self.rep)

        with self.run_with(fake):
            response = self.client.post(self.url, self.payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        wallet = DepartmentBMONIWallet.objects.get(department=self.department)
        self.assertEqual(wallet.status, DepartmentBMONIWallet.STATUS_FAILED)
        self.assertEqual(wallet.account_number, '')

    def test_a_duplicate_create_recovers_the_existing_person(self):
        # A 409 from BMONI means the first attempt LANDED: recover, don't retry.
        fake = FakeBMONI(conflict=True)
        self.auth(self.rep)

        with self.run_with(fake):
            response = self.client.post(self.url, self.payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        wallet = DepartmentBMONIWallet.objects.get(department=self.department)
        self.assertEqual(wallet.bmoni_user_id, 'recovered-user-1')
        self.assertEqual(wallet.account_number, '9845221370')

    def test_a_duplicate_holder_is_recovered_from_a_later_page(self):
        """
        The real user list is paginated (20 per page, 886 people in the sandbox),
        so a first-page-only lookup silently misses the person — which is what
        the live sandbox run caught. Both pages must be read.
        """
        fake = FakeBMONI(
            conflict=True,
            existing_user={
                'bmoniUserId': 'pages-deep',
                'email': 'holder@example.com',
            },
            existing_user_page=2,
        )
        self.auth(self.rep)

        with self.run_with(fake):
            response = self.client.post(self.url, self.payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        wallet = DepartmentBMONIWallet.objects.get(department=self.department)
        self.assertEqual(wallet.bmoni_user_id, 'pages-deep')

        pages = [call[1] for call in fake.calls if call[1].startswith('users?')]
        self.assertEqual(len(pages), 2)
        self.assertIn('page=2', pages[1])

    def test_the_recovery_lookup_matches_a_reformatted_phone_number(self):
        # BMONI's 409 does not say WHICH detail collided, so the phone is matched
        # with formatting ignored.
        fake = FakeBMONI(
            conflict=True,
            existing_user={
                'bmoniUserId': 'phone-match',
                'email': 'somebody-else@example.com',
                'phoneNumber': '+234 801-234-5678',
            },
        )
        self.auth(self.rep)

        with self.run_with(fake):
            response = self.client.post(self.url, self.payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        wallet = DepartmentBMONIWallet.objects.get(department=self.department)
        self.assertEqual(wallet.bmoni_user_id, 'phone-match')

    def test_a_duplicate_that_cannot_be_found_fails_loudly(self):
        fake = FakeBMONI(
            conflict=True,
            existing_user={
                'bmoniUserId': 'someone-else',
                'email': 'not-ours@example.com',
            },
            existing_user_page=2,
        )
        self.auth(self.rep)

        with self.run_with(fake):
            response = self.client.post(self.url, self.payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        wallet = DepartmentBMONIWallet.objects.get(department=self.department)
        self.assertEqual(wallet.status, DepartmentBMONIWallet.STATUS_FAILED)
        self.assertIn('could not be read back', wallet.last_error)

        # It stopped as soon as the list was exhausted instead of paging forever.
        pages = [call[1] for call in fake.calls if call[1].startswith('users?')]
        self.assertEqual(len(pages), 2)

    def test_a_holder_already_used_by_another_department_is_a_conflict(self):
        DepartmentBMONIWallet.objects.create(
            department=self.other_department,
            holder_email='holder@example.com',
            holder_phone='+2348012345678',
            bmoni_user_id='other-department-holder',
            account_name='Someone Else',
            account_number='1234567890',
            status=DepartmentBMONIWallet.STATUS_ACTIVE,
        )
        fake = FakeBMONI()
        self.auth(self.rep)

        with self.run_with(fake):
            response = self.client.post(self.url, self.payload(), format='json')

        # Two departments sharing one bank account is a conflict, and it is
        # decided before any money is asked for at BMONI.
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['error'], 'conflict')
        self.assertEqual(fake.calls, [])
        self.assertFalse(
            DepartmentBMONIWallet.objects.filter(
                department=self.department
            ).exists()
        )


class BMONIClientTests(BMONITestBase):
    """How the client talks to BMONI, and how it fails."""

    def test_the_partner_key_and_a_timeout_go_out_on_every_call(self):
        fake = FakeBMONI()
        with self.run_with(fake):
            BMONIClient().get_deposit_accounts('user-1')

        _method, _path, _body, kwargs = fake.calls[0]
        self.assertEqual(kwargs['headers']['x-api-key'], SANDBOX_SETTINGS['BMONI_API_KEY'])
        # The partner key is the x-api-key header — never a bearer token.
        self.assertNotIn('Authorization', kwargs['headers'])
        self.assertEqual(kwargs['timeout'], REQUEST_TIMEOUT)

    def test_a_gateway_error_is_retryable_not_a_crash(self):
        with patch(
            'apps.payments.bmoni_client.requests.request',
            lambda *args, **kwargs: FakeResponse(503, {'message': 'down'}),
        ):
            with self.assertRaises(BMONIUnavailable):
                BMONIClient().get_deposit_accounts('user-1')

    def test_an_unreadable_body_is_unavailable_not_a_crash(self):
        with patch(
            'apps.payments.bmoni_client.requests.request',
            lambda *args, **kwargs: FakeResponse(200, None),
        ):
            with self.assertRaises(BMONIUnavailable):
                BMONIClient().get_deposit_accounts('user-1')

    def test_a_list_valued_error_message_is_flattened(self):
        # BMONI's `message` is sometimes a list of strings.
        with patch(
            'apps.payments.bmoni_client.requests.request',
            lambda *args, **kwargs: FakeResponse(400, {'message': ['bad bvn', 'bad name']}),
        ):
            with self.assertRaises(BMONIError) as caught:
                BMONIClient().get_deposit_accounts('user-1')

        self.assertEqual(caught.exception.message, 'bad bvn; bad name')

    def test_an_unconfigured_client_never_calls_out(self):
        with override_settings(BMONI_API_KEY=''):
            with self.assertRaises(BMONINotConfigured):
                BMONIClient().get_deposit_accounts('user-1')


class BMONIWalletModelTests(BMONITestBase):
    """The invariants the model and the client hold on their own."""

    def test_the_db_refuses_an_active_wallet_with_no_account_number(self):
        with self.assertRaises(IntegrityError):
            # The inner atomic keeps the failed INSERT from poisoning the test's
            # own transaction.
            with transaction.atomic():
                DepartmentBMONIWallet.objects.create(
                    department=self.department,
                    status=DepartmentBMONIWallet.STATUS_ACTIVE,
                )

    def test_an_empty_nigerian_accounts_list_is_not_an_account(self):
        fake = FakeBMONI(deposit_accounts={
            'nigerianAccounts': [],
            'activationAccounts': DEPOSIT_ACCOUNTS['activationAccounts'],
        })
        with self.run_with(fake):
            account = BMONIClient().find_ngn_deposit_account('user-1')

        self.assertIsNone(account)

    def test_no_placeholder_wallet_address_is_used_against_production(self):
        client = BMONIClient(
            api_key='production-key', base_url='https://embedded.bmoni.com'
        )

        # Against production, a placeholder address would attach a real account
        # to a wallet nobody owns, so provisioning must refuse instead.
        with self.assertRaises(BMONIError):
            provision_ngn_account(
                client,
                first_name='Bunch',
                last_name='Dillon',
                email='holder@example.com',
                phone_number='+2348012345678',
                bvn='95888168924',
            )

    def test_the_sandbox_wallet_address_is_stable_for_one_department(self):
        first = sandbox_wallet_address(7)
        second = sandbox_wallet_address(7)

        # Stable on purpose: a random address would look like a different wallet
        # on every retry.
        self.assertEqual(first, second)
        self.assertEqual(len(first), 42)
        self.assertTrue(first.startswith('0x'))
        self.assertNotEqual(first, sandbox_wallet_address(8))


class BMONIWebhookTests(BMONITestBase):
    """POST /payments/bmoni/webhook/ — BMONI's deliveries, filed exactly once."""

    def setUp(self):
        super().setUp()
        self.webhook_url = '/api/payments/bmoni/webhook/'

    def sign(self, payload):
        # What BMONI signs: HMAC-SHA256 over the raw body.
        return hmac.new(
            SANDBOX_SETTINGS['BMONI_WEBHOOK_SECRET'].encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()

    def deliver(
        self, payload_obj, *, event_id='evt-1', signature=None, body=None
    ):
        """POST a delivery. `signature=''` sends no signature header at all."""
        payload = body if body is not None else json.dumps(payload_obj).encode()

        headers = {}
        if signature is None:
            headers['HTTP_X_WEBHOOK_SIGNATURE'] = self.sign(payload)
        elif signature:
            headers['HTTP_X_WEBHOOK_SIGNATURE'] = signature
        if event_id is not None:
            headers['HTTP_X_WEBHOOK_EVENT_ID'] = event_id

        return self.client.post(
            self.webhook_url, payload, content_type='application/json', **headers
        )

    def test_without_a_configured_secret_nothing_is_trusted(self):
        with override_settings(BMONI_WEBHOOK_SECRET=''):
            response = self.deliver({'event': 'employee.deposit.completed'})

        # Fail closed: without the subscription's secret a forgery is
        # indistinguishable from a real delivery, so nothing is filed.
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(BMONIWebhookEvent.objects.exists())

    def test_a_delivery_without_a_signature_is_a_400(self):
        response = self.deliver(
            {'event': 'employee.deposit.completed'}, signature=''
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(BMONIWebhookEvent.objects.exists())

    def test_a_wrong_signature_is_a_400(self):
        response = self.deliver(
            {'event': 'employee.deposit.completed'}, signature='deadbeef' * 8
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(BMONIWebhookEvent.objects.exists())

    def test_a_tampered_body_breaks_the_signature(self):
        # Signed one body, sent another: the classic "credit me twice" forgery.
        signed = json.dumps({
            'event': 'employee.deposit.completed',
            'data': {'amount': '10.00'},
        }).encode()
        tampered = json.dumps({
            'event': 'employee.deposit.completed',
            'data': {'amount': '1000000.00'},
        }).encode()

        response = self.deliver(None, signature=self.sign(signed), body=tampered)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(BMONIWebhookEvent.objects.exists())

    def test_a_body_that_is_not_json_is_a_400(self):
        payload = b'not json at all'

        response = self.deliver(None, signature=self.sign(payload), body=payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_valid_delivery_is_filed_with_the_payload_intact(self):
        response = self.deliver({
            'event': 'employee.deposit.completed',
            'data': {'amount': '500.00'},
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = BMONIWebhookEvent.objects.get()
        self.assertEqual(event.event_id, 'evt-1')
        self.assertEqual(event.event_type, 'employee.deposit.completed')
        self.assertEqual(event.raw_payload['data']['amount'], '500.00')
        # Archived, not credited: crediting deposits is Phase 2, so the row says
        # exactly that instead of pretending the money was applied.
        self.assertFalse(event.processed)
        self.assertIsNone(event.processed_at)

    def test_a_repeated_delivery_is_a_no_op(self):
        self.deliver({'event': 'employee.deposit.completed'}, event_id='evt-dup')
        second = self.deliver(
            {'event': 'employee.deposit.completed'}, event_id='evt-dup'
        )

        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(BMONIWebhookEvent.objects.count(), 1)

    def test_a_delivery_without_an_event_id_is_still_deduped(self):
        body = {'event': 'employee.deposit.completed'}

        self.deliver(body, event_id=None)
        self.deliver(body, event_id=None)

        self.assertEqual(BMONIWebhookEvent.objects.count(), 1)
        # No id from BMONI, so the body's own hash is the dedupe key: a retry of
        # the same delivery hashes the same, so it is still a no-op.
        self.assertTrue(
            BMONIWebhookEvent.objects.get().event_id.startswith('body-')
        )

    def test_a_deposit_is_linked_to_its_department(self):
        wallet = DepartmentBMONIWallet.objects.create(
            department=self.department,
            bmoni_user_id='ec6c5a47-70af-43f5-9559-b5230b8749d9',
            account_name='Dillon Bunch',
            account_number='9845221370',
            status=DepartmentBMONIWallet.STATUS_ACTIVE,
        )

        response = self.deliver({
            'event': 'employee.deposit.completed',
            'data': {
                'userId': 'ec6c5a47-70af-43f5-9559-b5230b8749d9',
                'amount': '500.00',
            },
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(BMONIWebhookEvent.objects.get().wallet_id, wallet.id)

    def test_an_event_about_an_unknown_holder_is_still_kept(self):
        response = self.deliver({
            'event': 'employee.withdrawal.completed',
            'data': {'userId': 'nobody-we-know'},
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = BMONIWebhookEvent.objects.get()
        self.assertIsNone(event.wallet)
        # Filed anyway: an event we cannot attribute is still evidence.
        self.assertEqual(event.event_type, 'employee.withdrawal.completed')
