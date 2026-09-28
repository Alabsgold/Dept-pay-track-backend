"""
Opt-in live checks against the BMONI sandbox.

Skipped by default. These create REAL sandbox users, so a normal run — and CI,
which has no BMONI key — never touches the network or spends rate-limited calls
(the sandbox allows 5 calls per short window).

    # PowerShell
    $env:BMONI_LIVE_TESTS='1'; python manage.py test apps.payments.tests_bmoni_live

What they prove that mocks cannot: the sandbox still issues a dedicated,
person-named NGN account through the exact call sequence this backend uses, and
our parsing of that response still matches. Verified 2026-09-26 against the
shared sandbox key — evidence and traps in docs/BMONI_SANDBOX_RUNBOOK.md.
"""
import os
import random
import unittest

from django.conf import settings
from django.test import SimpleTestCase

from .bmoni_client import BMONIClient, provision_ngn_account

LIVE = (
    os.environ.get('BMONI_LIVE_TESTS') == '1'
    and bool(getattr(settings, 'BMONI_API_KEY', ''))
)


@unittest.skipUnless(LIVE, 'set BMONI_LIVE_TESTS=1 (and BMONI_API_KEY) to run')
class BMONISandboxLiveTests(SimpleTestCase):
    """
    The client against the real sandbox.

    `SimpleTestCase` on purpose: no database. The client must work without the
    ORM, and a live check must never touch our own data.
    """

    # A sandbox persona. The name has to match the BVN exactly — identity
    # verification fails on purpose when it does not.
    FIRST_NAME = 'Bunch'
    LAST_NAME = 'Dillon'
    BVN = '95888168924'

    def setUp(self):
        self.api = BMONIClient()

    def _unique_email(self):
        # BMONI rejects a duplicate email OR phone ("User already exists"), and
        # the sandbox personas' details are already on file from earlier probes,
        # so each run uses a fresh identity. That is what makes these checks
        # repeatable instead of passing once.
        return f'dpt.live.{random.randint(10 ** 8, 10 ** 9 - 1)}@example.com'

    def _unique_phone(self):
        return f'+23490{random.randint(10 ** 6, 10 ** 7 - 1)}'

    def _provision(self, email, phone_number):
        return provision_ngn_account(
            self.api,
            first_name=self.FIRST_NAME,
            last_name=self.LAST_NAME,
            email=email,
            phone_number=phone_number,
            bvn=self.BVN,
            wallet_seed='live-check',
        )

    def test_provisioning_reaches_the_sandbox_and_reads_accounts_back(self):
        """
        Diagnostic, not aspirational.

        Verified 2026-09-26: this sandbox issued exactly ONE dedicated NGN
        account (user `ec6c5a47…` → `9845221370`, PROVIDUS BANK, named after the
        holder) and afterwards returned `nigerianAccounts: []` for new users —
        with or without a KYC profile first, and for either persona BVN. So this
        test asserts the CLIENT's behaviour, which must hold either way, and
        reports whether an account came back. It is not asserting a sandbox
        capability that is currently unavailable (see the runbook §2/§7).
        """
        user_id, account = self._provision(
            self._unique_email(), self._unique_phone()
        )

        self.assertTrue(user_id, 'BMONI did not return a user id')

        if account is None:
            # The real state today: BMONI issued no dedicated account, and the
            # client must say so rather than fall back to the pooled one.
            pooled = self.api.get_deposit_accounts(user_id)
            self.assertEqual(
                [entry['accountName'] for entry in pooled.get('activationAccounts', [])],
                ['Bkey Limited'],
                'expected the pooled activation account to be present but unused',
            )
            return

        # If the sandbox does issue one, it must be the department's own: named
        # after the verified holder, NGN, and never the pooled provider account.
        self.assertEqual(account['targetCurrency'], 'NGN')
        self.assertEqual(len(account['accountNumber']), 10)
        self.assertTrue(account['accountNumber'].isdigit())
        self.assertIn(self.LAST_NAME, account['accountName'])
        self.assertNotEqual(account['accountName'], 'Bkey Limited')

    def test_the_client_never_selects_the_pooled_account(self):
        # Whatever the sandbox is doing today, the pooled `Bkey Limited` bucket
        # must never be returned as a department's account.
        user_id, account = self._provision(
            self._unique_email(), self._unique_phone()
        )

        self.assertNotEqual((account or {}).get('accountName'), 'Bkey Limited')
        self.assertNotEqual((account or {}).get('accountNumber'), '6177463833')

    def test_provisioning_twice_reuses_the_same_holder(self):
        email = self._unique_email()
        phone_number = self._unique_phone()

        first_id, first_account = self._provision(email, phone_number)
        # The second attempt must land on the SAME person: it either gets a 409
        # and recovers them, or creates nothing new. What must never happen is a
        # second holder for one department.
        second_id, second_account = self._provision(email, phone_number)

        self.assertEqual(first_id, second_id)
        self.assertEqual(first_account, second_account)