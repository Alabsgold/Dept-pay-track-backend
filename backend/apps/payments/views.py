import requests
import hashlib
import hmac
import json
import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.contributions.models import Contribution
from apps.users.models import Department
from apps.users.permissions import IsClassRepOrAdmin
from .bmoni_client import (
    BMONIClient,
    BMONIError,
    BMONINotConfigured,
    provision_ngn_account,
)
from .models import BMONIWebhookEvent, DepartmentBMONIWallet, Payment, Transaction
from .serializers import DepartmentBankAccountSerializer, PaymentSerializer
from .permissions import IsAdminUser
from .serializers import UnverifiedPaymentSerializer

logger = logging.getLogger(__name__)


def _as_kobo(value):
    """Gateway money (kobo) as an exact int, or None when unusable."""
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _settle_gateway_charge(payment, paid_kobo):
    """
    Apply a gateway-reported charge to a Payment row (caller saves).

    One rule, used by BOTH the webhook and the verify endpoint so they can
    never disagree about the same charge:

      * exactly the agreed fee            -> success
      * more or less than the agreed fee  -> failed + refund review
      * an amount we cannot verify        -> failed + refund review (fail closed)
      * a fee this student already paid   -> failed + refund review (duplicate)

    A mismatch means the gateway really took the student's money for a fee we
    are not crediting, so it must never be silently absorbed: the row fails,
    `paid_amount` records what was actually taken, and the excess is flagged
    for a human to review before any refund is issued.

    A flag is only ever set while it is still `none`, so a reviewer's decision
    ('refunded'/'rejected') can never be overwritten by a later webhook retry.
    """
    expected_kobo = int(payment.amount * 100)
    actual_kobo = _as_kobo(paid_kobo)

    if actual_kobo is not None:
        # Kobo -> naira is exact at 2dp, so nothing is lost recording it.
        payment.paid_amount = (Decimal(actual_kobo) / 100).quantize(
            Decimal('0.01')
        )

    already_credited = (
        payment.contribution_id is not None
        and Payment.objects.filter(
            student_id=payment.student_id,
            contribution_id=payment.contribution_id,
            status=Payment.STATUS_SUCCESS,
        ).exclude(pk=payment.pk).exists()
    )

    if already_credited or actual_kobo is None or actual_kobo != expected_kobo:
        payment.status = Payment.STATUS_FAILED
        if payment.refund_status == Payment.REFUND_NONE:
            payment.refund_status = Payment.REFUND_PENDING_REVIEW
        return

    payment.status = Payment.STATUS_SUCCESS


def _save_settled(payment):
    """
    Persist a settled charge.

    If we LOSE the concurrent race — the DB's unique_success_per_student_fee
    constraint fires because another charge for this fee was credited a moment
    ago — the money was still taken, so the row fails into refund review
    instead of double-crediting. The student is never charged twice in our
    books, and the duplicate is queued for a human, not silently absorbed.
    """
    try:
        payment.save()
    except IntegrityError:
        payment.status = Payment.STATUS_FAILED
        if payment.refund_status == Payment.REFUND_NONE:
            payment.refund_status = Payment.REFUND_PENDING_REVIEW
        payment.save()


class PaymentListView(generics.ListAPIView):
    serializer_class = PaymentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Payment.objects.filter(student=self.request.user)


class InitializePaymentView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        contribution_id = request.data.get('contribution_id')
        if not contribution_id:
            return Response(
                {'error': 'bad_request', 'message': 'contribution_id is required.'},
                status=400
            )

        # A non-numeric id would raise inside the ORM lookup and crash the
        # request with a 500 — reject it cleanly instead.
        try:
            contribution_id = int(contribution_id)
        except (TypeError, ValueError):
            return Response(
                {'error': 'bad_request', 'message': 'contribution_id must be a number.'},
                status=400
            )

        # Scope the fee to THIS student's department + level, exactly like the
        # contributions list — a student may only pay fees that exist for them.
        # That list also hides expired fees, so this must too: otherwise a
        # student holding a stale id could still pay into a closed collection
        # and we would be holding money for a fee nobody is accepting.
        user = request.user
        contribution = Contribution.objects.filter(
            id=contribution_id,
            department=user.department,
        ).filter(
            Contribution.open_q()
        ).filter(
            Q(target_level__isnull=True) | Q(target_level=user.level)
        ).first()
        if contribution is None:
            return Response(
                {'error': 'not_found', 'message': 'Contribution not found or not available to you.'},
                status=404
            )

        # Duplicate protection: if already paid successfully, block re-payment.
        if Payment.objects.filter(
            student=user,
            contribution=contribution,
            status=Payment.STATUS_SUCCESS,
        ).exists():
            return Response(
                {'error': 'already_paid', 'message': 'You have already paid for this contribution.'},
                status=409
            )

        # The amount ALWAYS comes from the contribution — the student never
        # types a price. Server-side only.
        amount = contribution.amount

        amount_in_kobo = int(amount * 100)

        headers = {
            'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}',
            'Content-Type': 'application/json',
        }

        data = {
            'email': request.user.email,
            'amount': amount_in_kobo,
        }

        try:
            response = requests.post(
                'https://api.paystack.co/transaction/initialize',
                headers=headers,
                json=data,
                timeout=10
            )
        except requests.exceptions.RequestException:
            logger.warning('paystack initialize unavailable')
            return Response(
                {'error': 'gateway_unavailable', 'message': 'Payment gateway is unavailable. Try again shortly.'},
                status=502
            )

        try:
            result = response.json()
        except ValueError:
            # Non-JSON body (e.g. a proxy's HTML error page) — fail cleanly.
            logger.warning('paystack initialize invalid response status_code=%s', response.status_code)
            return Response(
                {'error': 'gateway_unavailable', 'message': 'Payment gateway is unavailable. Try again shortly.'},
                status=502
            )

        result_data = result.get('data') or {}

        if (
            not result.get('status')
            or not result_data.get('reference')
            or not result_data.get('authorization_url')
        ):
            logger.warning('paystack initialize rejected status_code=%s', response.status_code)
            return Response(
                {'error': 'gateway_unavailable', 'message': 'Payment gateway is unavailable. Try again shortly.'},
                status=502
            )

        payment = Payment.objects.create(
            student=request.user,
            contribution=contribution,
            # The contribution FK is the fee identity; this legacy column just
            # needs a valid choice value (never truncate the title into it).
            payment_type=Payment.PAYMENT_CONTRIBUTION,
            amount=amount,
            reference=result_data['reference'],
            status=Payment.STATUS_PENDING,
            method=Payment.METHOD_ONLINE,
        )

        return Response({
            'message': 'Payment initialized successfully.',
            # Contract §4 keys — the frontend reads checkout_url.
            'reference': payment.reference,
            'checkout_url': result_data['authorization_url'],
            # Kept for backward compatibility with earlier integrations.
            'authorization_url': result_data['authorization_url'],
            'payment': PaymentSerializer(payment).data,
        })


class VerifyPaymentView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, reference):
        # Resolve the payment LOCALLY first — an unknown reference should not
        # cost a round-trip to Paystack, and the local row is what we update.
        payment = Payment.objects.filter(
            reference=reference,
            student=request.user
        ).first()

        if not payment:
            # Contract §7: every error carries a machine code plus a message —
            # the frontend has exactly ONE error handler and reads
            # `payload.message`, so a bare sentence here would render blank.
            return Response(
                {'error': 'not_found', 'message': 'Payment not found.'},
                status=404
            )

        url = f'https://api.paystack.co/transaction/verify/{reference}'

        headers = {
            'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}',
        }

        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=10
            )
        except requests.exceptions.RequestException:
            # Gateway outage/timeout — our row stays untouched and the client
            # can retry, mirroring InitializePaymentView's 502 contract.
            logger.warning('paystack verify unavailable')
            return Response(
                {'error': 'gateway_unavailable', 'message': 'Payment gateway is unavailable. Try again shortly.'},
                status=502
            )
        try:
            result = response.json()
        except ValueError:
            # Non-JSON body (e.g. a proxy's HTML error page) — fail cleanly.
            logger.warning('paystack verify invalid response status_code=%s', response.status_code)
            return Response(
                {'error': 'gateway_unavailable', 'message': 'Payment gateway is unavailable. Try again shortly.'},
                status=502
            )

        if not result.get('status') or not result.get('data'):
            logger.warning('paystack verify rejected status_code=%s', response.status_code)
            return Response(
                {'error': 'gateway_unavailable', 'message': 'Payment gateway is unavailable. Try again shortly.'},
                status=502
            )

        paystack_status = result['data'].get('status')

        # Only a TERMINAL outcome may change our row. Paystack's non-terminal
        # statuses — 'abandoned' (student closed the checkout) and 'pending' /
        # 'ongoing' / 'processing' (charge not settled yet) — must leave the
        # payment exactly as it is. Paystack raises no `charge.failed` webhook
        # event, so treating "not success (yet)" as failure here was what
        # flipped payments to `failed` that were never actually declined.
        if paystack_status == 'success':
            # Money moved — but this endpoint is also the student's own path
            # back to the truth, so it applies the SAME amount rule as the
            # webhook. Trusting the status alone would credit a fee the student
            # never actually paid in full.
            _settle_gateway_charge(payment, result['data'].get('amount'))
            _save_settled(payment)
        elif paystack_status in ('failed', 'reversed'):
            # Declined, or the charge was reversed: no money is being kept, so
            # there is nothing to refund — just record the outcome.
            payment.status = Payment.STATUS_FAILED
            payment.save()

        return Response({
            'message': 'Payment verification completed.',
            'payment': PaymentSerializer(payment).data,
        })

class PaymentDetailView(generics.RetrieveAPIView):
    serializer_class = PaymentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Payment.objects.filter(student=self.request.user)

class PaystackWebhookView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        payload = request.body

        signature = request.headers.get('x-paystack-signature')

        if not signature:
            # Gateway-facing, but §7's shape is uniform across every endpoint.
            return Response(
                {'error': 'bad_request', 'message': 'Missing signature.'},
                status=400
            )

        expected_signature = hmac.new(
            settings.PAYSTACK_SECRET_KEY.encode(),
            payload,
            hashlib.sha512
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_signature):
            return Response(
                {'error': 'bad_request', 'message': 'Invalid signature.'},
                # Contract §7: invalid webhook signature → 400, not 401.
                status=400
            )

        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            return Response(
                {'error': 'bad_request', 'message': 'Invalid payload.'},
                status=400
            )

        if not isinstance(data, dict):
            return Response(
                {'error': 'bad_request', 'message': 'Invalid payload.'},
                status=400
            )

        if data.get('event') == 'charge.success':
            transaction = data.get('data', {})
            reference = transaction.get('reference')

            payment = Payment.objects.filter(
                reference=reference
            ).first()

            # Audit proof: store exactly what the gateway said. FIRST delivery
            # wins — Paystack's identical retries are not duplicated — and
            # events for references we don't recognise are still kept
            # (payment=None) for forensics.
            if reference:
                Transaction.objects.get_or_create(
                    reference=reference,
                    defaults={'payment': payment, 'raw_payload': data},
                )
            logger.info(
                'paystack webhook event=%s reference=%s payment_found=%s',
                data.get('event'), reference, payment is not None,
            )

            if payment:
                # Idempotency: a retried webhook must not re-process/overwrite.
                if payment.status == Payment.STATUS_SUCCESS:
                    return Response({'received': True})

                # The amount is checked inside the shared rule: exact fee ->
                # success; anything else (over, under, unverifiable, or a fee
                # already paid) -> failed + refund review.
                _settle_gateway_charge(payment, transaction.get('amount'))
                _save_settled(payment)
                logger.info(
                    'payment %s settled status=%s refund_status=%s',
                    payment.reference, payment.status, payment.refund_status,
                )

        return Response({'received': True})


class UnverifiedPaymentsView(APIView):
    """
    GET /api/payments/unverified/ — admin-only refund-review queue.

    Lists every payment flagged `pending_review`: the gateway took the
    student's money for a fee we could not credit (wrong amount, duplicate
    charge, or an amount we could not verify), so a human must decide a
    refund. Strictly read-only — approving or refusing a refund happens in
    the Django admin; this endpoint never mutates anything.
    """

    permission_classes = [IsAdminUser]

    def get(self, request):
        flagged = Payment.objects.filter(
            refund_status=Payment.REFUND_PENDING_REVIEW
        ).order_by('-created_at')

        serializer = UnverifiedPaymentSerializer(flagged, many=True)
        return Response({
            'count': flagged.count(),
            'results': serializer.data,
        })


# --- BMONI Embedded: a department's own NGN bank account --------------------
#
# The department's account is issued by BMONI for a real, BVN-verified person
# (the department's nominated account holder), then stored on the wallet row so
# a student's payment screen never depends on BMONI being reachable. Verified
# against the sandbox on 2026-09-26 — see docs/BMONI_SANDBOX_RUNBOOK.md for the
# full walkthrough and the traps this code avoids.


def _is_admin(request):
    """
    The admin definition, via the ONE permission class that owns it.

    `IsAdminUser.has_permission` ignores its `view` argument, so passing None
    keeps a single source of truth for "admin" (role, staff, or superuser)
    instead of re-testing the same three fields here.
    """
    return IsAdminUser().has_permission(request, None)


def _as_bvn(value):
    """An 11-digit Nigerian BVN, or None. Checked here, before it is sent."""
    bvn = str(value or '').strip()
    if len(bvn) == 11 and bvn.isdigit():
        return bvn
    return None


def _scrub(text, secret):
    """
    Keep an entered identifier out of logs, columns and responses.

    An upstream error message can echo back what we sent it, so anything that
    came from the user (the BVN) is masked before we log it or store it. The
    message itself is still shown — it is often the only useful explanation.
    """
    text = str(text or '')
    if secret:
        text = text.replace(str(secret), '***')
    return text


def _not_provisioned_message(wallet):
    """Why there is no account to show yet, in words a member can act on."""
    if wallet is None:
        return 'This department has no bank account yet.'
    if wallet.status == DepartmentBMONIWallet.STATUS_FAILED:
        return 'Setting up this department\'s bank account failed. A class rep can try again.'
    return 'This department\'s bank account is still being set up.'


def _provisioning_failure(exc, bvn=None):
    """
    Map a BMONI failure onto the contract's error shape.

    Only a rejection of what we SENT (400/409/422) is the caller's problem and
    becomes a `400`; a rejected key, a 5xx or a timeout is ours, so it becomes a
    `502 gateway_unavailable` with a generic message. The operator still gets
    BMONI's own words on the wallet row (`last_error`), sanitized.
    """
    if exc.status_code in (400, 409, 422):
        return Response(
            {'error': 'bad_request', 'message': _scrub(exc.message, bvn)},
            status=400,
        )
    return Response(
        {
            'error': 'gateway_unavailable',
            'message': 'The bank account service is unavailable. Try again shortly.',
        },
        status=502,
    )


class DepartmentBankAccountView(APIView):
    """
    GET/POST /api/payments/departments/{id}/bank-account/

    The department's own NGN virtual account — what a contributor pays into when
    they transfer from their bank instead of using the Paystack card flow.

    GET  — any authenticated member of that department (reps/admins included):
           students are the audience for this account, so everyone who might pay
           it can read it. "No account yet" is a `200` with `provisioned: false`,
           never a 404 — that is a normal state the UI renders as "ask your rep",
           not an error.
    POST — class rep (their own department only) or admin. Idempotent: a
           department that already has an account gets the SAME account back with
           no second call to BMONI, so pressing the button twice cannot open a
           second account.
    """

    def get_permissions(self):
        """
        Reading the account is for its members; creating one is a rep/admin job.

        Two audiences on one URL: a student must be able to see where to pay, but
        must not be able to open a real bank account in the department's name.
        """
        if self.request.method == 'POST':
            return [IsClassRepOrAdmin()]
        return [permissions.IsAuthenticated()]

    def _department(self, request, pk):
        """
        The department, or 404 when this caller may not see it.

        A department the caller cannot see must be indistinguishable from one
        that does not exist, so both answer 404 — the rule the payment and
        contribution endpoints already follow. For a rep, the scoped lookup is
        also what keeps them inside their own department: another department's
        account is simply not there.
        """
        if _is_admin(request):
            return get_object_or_404(Department, pk=pk)
        return get_object_or_404(Department, pk=pk, id=request.user.department_id)

    def _body(self, department, wallet):
        """
        The one response shape, used by both GET and a successful POST.

        `bank_account` is null until an account really exists, and the account
        details come from the stored row — which only ever holds what BMONI
        issued.
        """
        provisioned = (
            wallet is not None
            and wallet.status == DepartmentBMONIWallet.STATUS_ACTIVE
        )
        body = {
            'provisioned': provisioned,
            'department': {
                'id': department.id,
                'name': department.name,
                'faculty': department.faculty,
            },
            'bank_account': (
                DepartmentBankAccountSerializer(wallet).data
                if provisioned else None
            ),
            'status': (
                wallet.status if wallet
                else DepartmentBMONIWallet.STATUS_PENDING
            ),
        }
        if not provisioned:
            body['message'] = _not_provisioned_message(wallet)
        return body

    def get(self, request, pk):
        department = self._department(request, pk)
        wallet = DepartmentBMONIWallet.objects.filter(
            department=department
        ).first()
        return Response(self._body(department, wallet))

    def post(self, request, pk):
        department = self._department(request, pk)

        # Read before write: an account that already exists is returned as it is,
        # with no second call to BMONI. BMONI has no idempotency keys, so this
        # check — not a retry policy — is what stops a second account or a second
        # person being created by an impatient second press.
        wallet = DepartmentBMONIWallet.objects.filter(
            department=department
        ).first()
        if wallet and wallet.status == DepartmentBMONIWallet.STATUS_ACTIVE:
            return Response(self._body(department, wallet))

        data = request.data
        first_name = str(data.get('first_name') or '').strip()
        last_name = str(data.get('last_name') or '').strip()
        email = str(data.get('email') or '').strip()
        phone_number = str(data.get('phone_number') or '').strip()
        bvn = _as_bvn(data.get('bvn'))

        missing = [
            label
            for label, value in (
                ('first_name', first_name),
                ('last_name', last_name),
                ('email', email),
                ('phone_number', phone_number),
            )
            if not value
        ]
        if bvn is None:
            missing.append('bvn (11 digits)')
        if missing:
            return Response(
                {
                    'error': 'bad_request',
                    'message': 'Missing or invalid: ' + ', '.join(missing) + '.',
                },
                status=400,
            )

        try:
            wallet_index = int(data.get('ngn_wallet_index') or 1)
        except (TypeError, ValueError):
            wallet_index = 1

        # One BMONI person backs at most one department account. Two departments
        # sharing a bank account is the kind of thing nobody notices until the
        # money lands, so a holder already on another department is a conflict.
        # Email and phone are both unique at BMONI, so either matching another
        # department's holder is the same clash.
        clash = DepartmentBMONIWallet.objects.filter(
            Q(holder_email__iexact=email) | Q(holder_phone=phone_number)
        )
        if wallet is not None:
            clash = clash.exclude(pk=wallet.pk)
        clash = clash.first()
        if clash is not None:
            return Response(
                {
                    'error': 'conflict',
                    'message': (
                        f'{clash.department.name} already has a bank account for '
                        'this holder. Use different holder details.'
                    ),
                },
                status=409,
            )

        wallet = wallet or DepartmentBMONIWallet(department=department)
        wallet.holder_first_name = first_name
        wallet.holder_last_name = last_name
        wallet.holder_email = email
        wallet.holder_phone = phone_number
        # Last four digits only: the full BVN is never stored anywhere.
        wallet.holder_bvn_last4 = bvn[-4:]
        # A retry clears a previous failure back to "in progress".
        wallet.status = DepartmentBMONIWallet.STATUS_PENDING
        wallet.save()

        try:
            user_id, account = provision_ngn_account(
                BMONIClient(),
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone_number=phone_number,
                bvn=bvn,
                ngn_wallet_address=(
                    str(data.get('ngn_wallet_address') or '').strip() or None
                ),
                ngn_wallet_index=wallet_index,
                personal_info=data.get('personal_info') or None,
                address=data.get('address') or None,
                wallet_seed=department.id,
            )
        except BMONINotConfigured:
            wallet.mark_failed('BMONI is not configured on this server.')
            wallet.save()
            return Response(
                {
                    'error': 'unavailable',
                    'message': 'Bank account setup is not available on this server.',
                },
                status=503,
            )
        except BMONIError as exc:
            # Sanitized before it is stored, logged, or returned: an upstream
            # message can echo back what we sent it.
            reason = _scrub(exc.message, bvn)
            wallet.mark_failed(reason)
            wallet.save()
            logger.warning(
                'bmoni provisioning failed for department %s: %s',
                department.id, reason,
            )
            return _provisioning_failure(exc, bvn)

        if not account or not account.get('accountNumber'):
            # Onboarding came back without an account number. Recording it as a
            # failure is the honest option: showing students an empty "pay here"
            # card would collect money into an account nobody can name.
            wallet.mark_failed(
                'BMONI did not return an NGN account for this holder.'
            )
            wallet.save()
            return Response(
                {
                    'error': 'gateway_unavailable',
                    'message': (
                        'The bank account service did not return an account. '
                        'Try again shortly.'
                    ),
                },
                status=502,
            )

        wallet.mark_active(account, bmoni_user_id=user_id)
        try:
            wallet.save()
        except IntegrityError:
            # The DB's unique `bmoni_user_id` fired: between the conflict check
            # above and this save, another department claimed the same BMONI
            # person. Fail this attempt rather than let two departments share an
            # account nobody can tell apart.
            wallet.bmoni_user_id = None
            wallet.mark_failed(
                'Another department already uses this account holder.'
            )
            wallet.save()
            return Response(
                {
                    'error': 'conflict',
                    'message': (
                        'This account holder is already used by another department.'
                    ),
                },
                status=409,
            )
        logger.info(
            'bmoni issued an NGN account for department %s (%s, ending %s)',
            department.id, wallet.bank_name, wallet.account_number[-4:],
        )
        return Response(self._body(department, wallet), status=201)


def _find_user_id(data):
    """
    The BMONI user id inside a webhook payload, when it states one.

    Deliberately conservative: the exact envelope of a live BMONI delivery is
    not yet confirmed (we are still on the shared sandbox key, whose
    subscription points at another partner — see the runbook), so only explicit
    id keys are read and nothing is guessed. A missing link costs one audit
    join; a WRONG link would attach a deposit to the wrong department.
    """
    if not isinstance(data, dict):
        return None

    candidates = [data.get('bmoniUserId'), data.get('userId')]
    for key in ('data', 'user', 'employee', 'wallet', 'account'):
        nested = data.get(key)
        if isinstance(nested, dict):
            candidates.append(nested.get('bmoniUserId'))
            candidates.append(nested.get('userId'))

    return next(
        (value for value in candidates if isinstance(value, str) and value),
        None,
    )


def _wallet_for_event(data):
    """The department wallet an event is about, or None when we cannot tell."""
    user_id = _find_user_id(data)
    if not user_id:
        return None
    return DepartmentBMONIWallet.objects.filter(bmoni_user_id=user_id).first()


class BMONIWebhookView(APIView):
    """
    POST /api/payments/bmoni/webhook/ — BMONI calls this automatically.

    No auth token, because BMONI cannot hold one. Authenticity comes from the
    signature instead: HMAC-SHA256 over the **raw** request body in
    `x-webhook-signature`, keyed with our subscription's secret
    (`BMONI_WEBHOOK_SECRET`). The body is therefore verified BEFORE it is
    parsed — re-serialising JSON first would change the bytes and break the
    check. An unsigned or wrongly-signed delivery is a `400`, exactly as the
    Paystack webhook treats its own.

    BMONI retries deliveries, so `x-webhook-event-id` is the dedupe key: the
    event is stored once (`event_id` is unique) and a repeat is a no-op `200`
    rather than a second row. What gets stored is what BMONI actually said.

    Phase 1 archives events without crediting them, so they land
    `processed=False` as the Phase 2 worklist — crediting a deposit needs our
    own partner key and sandbox test tokens (docs/BMONI_SANDBOX_RUNBOOK.md).
    A `200` here means "received and filed", which is all this can honestly
    promise today.
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        secret = settings.BMONI_WEBHOOK_SECRET
        if not secret:
            # Fail closed. Without the subscription's secret, a forged delivery
            # is indistinguishable from a real one, so nothing is trusted and
            # BMONI keeps retrying until the server is configured.
            logger.warning(
                'bmoni webhook delivery ignored: BMONI_WEBHOOK_SECRET is unset'
            )
            return Response(
                {'error': 'unavailable', 'message': 'Webhook is not configured.'},
                status=503,
            )

        payload = request.body
        signature = request.headers.get('x-webhook-signature')

        if not signature:
            return Response(
                {'error': 'bad_request', 'message': 'Missing signature.'},
                status=400,
            )

        expected_signature = hmac.new(
            secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_signature):
            return Response(
                {'error': 'bad_request', 'message': 'Invalid signature.'},
                status=400,
            )

        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            return Response(
                {'error': 'bad_request', 'message': 'Invalid payload.'},
                status=400,
            )

        if not isinstance(data, dict):
            return Response(
                {'error': 'bad_request', 'message': 'Invalid payload.'},
                status=400,
            )

        # Dedupe key is BMONI's own event id. If a delivery ever arrives without
        # one, the body's hash stands in: a retry of the same delivery hashes
        # identically, so it is still a no-op instead of a second row.
        event_id = request.headers.get('x-webhook-event-id') or (
            'body-' + hashlib.sha256(payload).hexdigest()
        )

        event_type = data.get('event') or data.get('type') or ''

        _, created = BMONIWebhookEvent.objects.get_or_create(
            event_id=event_id[:128],
            defaults={
                'event_type': str(event_type)[:100],
                'wallet': _wallet_for_event(data),
                'raw_payload': data,
            },
        )

        if not created:
            # A retry of something already filed: nothing to do.
            logger.info('bmoni webhook event %s already filed', event_id)
            return Response({'received': True})

        logger.info(
            'bmoni webhook filed event=%s id=%s (archived unprocessed: '
            'deposit crediting is Phase 2)',
            event_type, event_id,
        )
        return Response({'received': True})