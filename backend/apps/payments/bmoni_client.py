"""
BMONI Embedded API client — a department's own NGN virtual bank account.

Thin on purpose: one partner key, one base URL, one `requests` session per call,
and every failure turned into a typed exception the view can map to a contract
error code. No abstraction layer, no retry theatre — BMONI has no idempotency
keys, so a blind retry can fork a second person or a second wallet. The rule
this module enforces instead is **read before you write**: recover an existing
user (a duplicate create answers 409, which means the first attempt landed) and
read an existing account back, rather than creating twice.

Facts this module encodes, all verified against the sandbox on 2026-09-26
(walkthrough and evidence: docs/BMONI_SANDBOX_RUNBOOK.md):

  * the key travels in the `x-api-key` header, not `Authorization`;
  * the partner is derived from the API key, so the deprecated `partnerId`
    body field is never sent;
  * a deposit-accounts response holds TWO Nigerian-looking buckets, and only one
    of them is the department's money — see `find_ngn_deposit_account`;
  * upstream error bodies carry `{statusCode, message, error}` where `message`
    may be a string OR a list, so it is normalised here and never parsed by
    callers.
"""

import hashlib
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Sandbox is the default (settings.BMONI_BASE_URL). Production is a different
# host and must be set explicitly in the environment — never in code.
DEFAULT_BASE_URL = 'https://embedded-dev.bmoni.com'
PRODUCTION_BASE_URL = 'https://embedded.bmoni.com'

# A hung gateway must not hang a Django request/worker.
REQUEST_TIMEOUT = 10

# BMONI's own duplicate-create status: the person already exists, which is a
# SUCCESS for our purposes (see create_or_recover_user).
STATUS_CONFLICT = 409


class BMONIError(Exception):
    """
    A BMONI call failed in a way the caller must handle.

    `status_code` is the upstream status when there was a response (None when
    there was not, e.g. a timeout). `message` is always safe to surface: an
    upstream body can echo back request data, so raw bodies are logged here and
    never copied verbatim into an API response.
    """

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class BMONINotConfigured(BMONIError):
    """No `BMONI_API_KEY` in the environment: the feature is switched off."""


class BMONIUnavailable(BMONIError):
    """Network failure, timeout, or a 5xx from BMONI — worth retrying later."""


def _upstream_message(data):
    """BMONI's error text as one string. `message` may be a str OR a list."""
    if isinstance(data, dict):
        message = data.get('message')
        if isinstance(message, (list, tuple)):
            return '; '.join(str(part) for part in message if part)
        if message:
            return str(message)
    return 'BMONI rejected the request.'


def _phone_digits(value):
    """A phone number as digits only, so `+234 801 234 5678` == `+2348012345678`."""
    return ''.join(char for char in str(value or '') if char.isdigit())


def _payload_items(data, *keys):
    """Items out of a list-ish response, tolerating either envelope."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return value
        # A single object response (`{"user": {...}}`).
        for key in keys:
            value = data.get(key)
            if isinstance(value, dict):
                return [value]
    return []


class BMONIClient:
    """
    The BMONI calls this project makes, one method each.

    Instantiate per request: it holds nothing but the key and base URL, so there
    is no session state to keep alive between calls.
    """

    # Recovery-search bounds. The partner list is shared and paginated (20 per
    # page by default, ~900 people in the sandbox), so a lookup has to page — but
    # an unbounded scan would be a denial of our own service. 10 x 100 = 1,000
    # people, comfortably above both.
    SEARCH_PAGE_SIZE = 100
    SEARCH_MAX_PAGES = 10

    def __init__(self, api_key=None, base_url=None):
        self.api_key = settings.BMONI_API_KEY if api_key is None else api_key
        self.base_url = (
            base_url if base_url is not None else settings.BMONI_BASE_URL
        ) or DEFAULT_BASE_URL
        self.base_url = self.base_url.rstrip('/')

    @property
    def configured(self):
        return bool(self.api_key)

    @property
    def is_sandbox(self):
        """True unless we have been pointed at the production host."""
        return self.base_url != PRODUCTION_BASE_URL

    def _request(self, method, path, body=None):
        """
        One call. Every failure mode becomes a typed exception, and nothing
        upstream is ever logged verbatim except the network reason.
        """
        if not self.configured:
            raise BMONINotConfigured('BMONI API key is not configured.')

        try:
            response = requests.request(
                method,
                f'{self.base_url}{path}',
                json=body,
                headers={
                    'x-api-key': self.api_key,
                    'Content-Type': 'application/json',
                },
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.warning('bmoni %s %s unreachable: %s', method, path, exc)
            raise BMONIUnavailable(
                'BMONI is unavailable. Try again shortly.'
            ) from exc

        if response.status_code >= 500:
            logger.warning(
                'bmoni %s %s -> %s', method, path, response.status_code
            )
            raise BMONIUnavailable(
                'BMONI is unavailable. Try again shortly.',
                response.status_code,
            )

        try:
            data = response.json()
        except ValueError:
            logger.warning(
                'bmoni %s %s -> unreadable body (%s)',
                method, path, response.status_code,
            )
            raise BMONIUnavailable(
                'BMONI returned an unreadable response.',
                response.status_code,
            )

        if response.status_code >= 400:
            raise BMONIError(
                _upstream_message(data), response.status_code
            )

        return data

    def create_user(
        self, *, first_name, last_name, email, phone_number, bvn=None
    ):
        """POST /v1/users — create the person who will own the account."""
        body = {
            'firstName': first_name,
            'lastName': last_name,
            'email': email,
            'phoneNumber': phone_number,
        }
        if bvn:
            body['bvn'] = bvn
        return self._request('POST', '/v1/users', body)

    def find_existing_user(self, *, email=None, phone_number=None):
        """
        The person a duplicate create collided with — looked up by email OR phone.

        BMONI's 409 says a person exists but not *which* detail collided (email
        and phone are both unique: `User already exists with this email`), so
        both are matched. The lookup MUST page: `GET /v1/users` is paginated
        (20 per page by default) and this is a shared partner list — in the
        sandbox it already holds 886 people. A first-page-only search silently
        misses the person, which turns "recover, don't duplicate" into a dead
        end. Verified 2026-09-26.
        """
        wanted_email = (email or '').strip().lower()
        wanted_phone = _phone_digits(phone_number)
        if not wanted_email and not wanted_phone:
            return None

        for page in range(1, self.SEARCH_MAX_PAGES + 1):
            data = self._request(
                'GET',
                f'/v1/users?page={page}&limit={self.SEARCH_PAGE_SIZE}',
            )
            users = _payload_items(data, 'users', 'data', 'items')
            if not users:
                return None

            for user in users:
                if wanted_email and (
                    (user.get('email') or '').strip().lower() == wanted_email
                ):
                    return user
                if wanted_phone and (
                    _phone_digits(user.get('phoneNumber')) == wanted_phone
                ):
                    return user

            total = data.get('total') if isinstance(data, dict) else None
            if isinstance(total, int) and page * self.SEARCH_PAGE_SIZE >= total:
                return None

        # Budget spent without a match: say so rather than guess.
        logger.warning(
            'bmoni user lookup hit its page budget (%s pages)', self.SEARCH_MAX_PAGES
        )
        return None

    def create_or_recover_user(
        self, *, first_name, last_name, email, phone_number, bvn=None
    ):
        """
        Create the account holder, or return the one a previous attempt made.

        A 409 from BMONI is not an error: it means this person already exists,
        so we read them back. That single behaviour is what makes an operator
        pressing "provision" twice safe.

        Returns `(user, created)`.
        """
        try:
            data = self.create_user(
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone_number=phone_number,
                bvn=bvn,
            )
        except BMONIError as exc:
            if exc.status_code != STATUS_CONFLICT:
                raise
            existing = self.find_existing_user(
                email=email, phone_number=phone_number
            )
            if existing is None:
                raise BMONIError(
                    f'BMONI already has this person ({exc.message}) but they '
                    'could not be read back, so nothing was created.',
                    STATUS_CONFLICT,
                ) from exc
            return existing, False

        created = _payload_items(data, 'user', 'data')
        # Fall back to the response itself if it is the bare object.
        return (created[0] if created else data), True

    def patch_kyc(self, user_id, *, personal_info=None, address=None):
        """
        PATCH /v1/users/{id}/kyc — sends only the sections we actually hold.

        Skipped entirely (no HTTP call) when we have neither, so a caller can
        always invoke it without inventing data.
        """
        body = {}
        if personal_info:
            body['personalInfo'] = personal_info
        if address:
            body['address'] = address
        if not body:
            return {}
        return self._request('PATCH', f'/v1/users/{user_id}/kyc', body)

    def start_nigeria_onboarding(
        self, user_id, *, bvn, ngn_wallet_address, ngn_wallet_index=1
    ):
        """
        POST /v1/users/{id}/onboarding/start-nigeria — the call that ISSUES the
        NGN virtual account.

        `ngnWalletAddress`/`ngnWalletIndex` are the on-chain wallet coordinates
        the account gets linked to; production needs real ones, so what to send
        is the caller's decision, not this module's (see `provision_ngn_account`).
        """
        return self._request(
            'POST',
            f'/v1/users/{user_id}/onboarding/start-nigeria',
            {
                'bvn': bvn,
                'ngnWalletAddress': ngn_wallet_address,
                'ngnWalletIndex': ngn_wallet_index,
            },
        )

    def get_deposit_accounts(self, user_id):
        """GET /v1/users/{id}/bank-accounts/deposit-accounts."""
        return self._request(
            'GET', f'/v1/users/{user_id}/bank-accounts/deposit-accounts'
        )

    def find_ngn_deposit_account(self, user_id):
        """
        The department's OWN NGN account out of a deposit-accounts response.

        Two buckets in that response look Nigerian, and only one is the
        department's money:

          * `nigerianAccounts` — the account issued to the KYC holder and named
            after them, `targetCurrency: "NGN"`. THIS is the one a student pays.
          * `activationAccounts` — a POOLED provider account (verified in the
            sandbox: `Bkey Limited`, 9 Payment Service Bank, `targetCurrency
            "EUR"`). Showing it would send contributions to a name that is not
            the department's, so it is never returned from here.

        Returns None when there is no account yet: that is a real state, not an
        error, and the caller decides what to tell the user.
        """
        data = self.get_deposit_accounts(user_id)
        accounts = data.get('nigerianAccounts') if isinstance(data, dict) else None
        accounts = accounts or []

        for account in accounts:
            if (account.get('targetCurrency') or account.get('currency')) == 'NGN':
                return account

        # No targetCurrency we recognise: this bucket IS the Nigerian one, so a
        # single entry is still the account rather than nothing.
        return accounts[0] if accounts else None


def sandbox_wallet_address(seed):
    """
    A deterministic placeholder wallet address, for SANDBOX use only.

    `start-nigeria` wants the on-chain wallet coordinates the account will be
    linked to. Real ones need an owner-signed smart wallet, which is Phase 2
    (documents/BMONI_SANDBOX_RUNBOOK.md §2). The sandbox accepts a well-formed
    address and still issues the account, so this derives a stable 40-hex
    address from the seed — the same department always gets the same address,
    because a random one would look like a different wallet on every retry.

    Production must never use this; `provision_ngn_account` refuses to.
    """
    digest = hashlib.sha256(str(seed).encode()).hexdigest()
    return f'0x{digest[:40]}'


def provision_ngn_account(
    client,
    *,
    first_name,
    last_name,
    email,
    phone_number,
    bvn,
    ngn_wallet_address=None,
    ngn_wallet_index=1,
    personal_info=None,
    address=None,
    wallet_seed=None,
):
    """
    Create (or recover) the account holder, then get their NGN account.

    Returns `(user_id, account_or_None)`. Order matters: BMONI issues the
    virtual account from the onboarding call, so a failure at any step leaves
    the caller with a person to retry against rather than half an account.

    Read-before-write throughout — an existing person is recovered rather than
    duplicated, and the account is read back from BMONI instead of being assumed
    from a 200 on onboarding.
    """
    if ngn_wallet_address is None:
        if not client.is_sandbox:
            raise BMONIError(
                'An NGN wallet address is required against production: a '
                'placeholder would attach the account to a wallet nobody owns.'
            )
        ngn_wallet_address = sandbox_wallet_address(wallet_seed or email)

    user, _created = client.create_or_recover_user(
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone_number=phone_number,
        bvn=bvn,
    )

    user_id = user.get('bmoniUserId') or user.get('id')
    if not user_id:
        raise BMONIError('BMONI did not return an account holder id.')

    client.patch_kyc(user_id, personal_info=personal_info, address=address)
    client.start_nigeria_onboarding(
        user_id,
        bvn=bvn,
        ngn_wallet_address=ngn_wallet_address,
        ngn_wallet_index=ngn_wallet_index,
    )

    return user_id, client.find_ngn_deposit_account(user_id)
