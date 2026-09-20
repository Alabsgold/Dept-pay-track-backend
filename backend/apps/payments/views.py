import requests
import hashlib
import hmac
import json
import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.contributions.models import Contribution
from .models import Payment, Transaction
from .serializers import PaymentSerializer
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
            Q(deadline__isnull=True) | Q(deadline__gte=timezone.now())
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
            return Response(
                {'error': 'Payment not found.'},
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
            return Response(
                {'error': 'Missing signature.'},
                status=400
            )

        expected_signature = hmac.new(
            settings.PAYSTACK_SECRET_KEY.encode(),
            payload,
            hashlib.sha512
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_signature):
            return Response(
                {'error': 'Invalid signature.'},
                # Contract §7: invalid webhook signature → 400, not 401.
                status=400
            )

        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            return Response(
                {'error': 'Invalid payload.'},
                status=400
            )

        if not isinstance(data, dict):
            return Response(
                {'error': 'Invalid payload.'},
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