import requests
import hashlib
import hmac
import json

from django.conf import settings
from django.db.models import Q
from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.contributions.models import Contribution
from .models import Payment
from .serializers import PaymentSerializer


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
        user = request.user
        contribution = Contribution.objects.filter(
            id=contribution_id,
            department=user.department,
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
            return Response(
                {'error': 'gateway_unavailable', 'message': 'Payment gateway is unavailable. Try again shortly.'},
                status=502
            )

        try:
            result = response.json()
        except ValueError:
            # Non-JSON body (e.g. a proxy's HTML error page) — fail cleanly.
            return Response(
                {'error': 'gateway_unavailable', 'message': 'Payment gateway returned an invalid response. Try again shortly.'},
                status=502
            )

        result_data = result.get('data') or {}

        if (
            not result.get('status')
            or not result_data.get('reference')
            or not result_data.get('authorization_url')
        ):
            return Response(
                {
                    'error': 'Unable to initialize payment.',
                    'details': result
                },
                status=400
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
            return Response(
                {'error': 'gateway_unavailable', 'message': 'Payment gateway is unavailable. Try again shortly.'},
                status=502
            )
        try:
            result = response.json()
        except ValueError:
            # Non-JSON body (e.g. a proxy's HTML error page) — fail cleanly.
            return Response(
                {'error': 'gateway_unavailable', 'message': 'Payment gateway returned an invalid response. Try again shortly.'},
                status=502
            )

        if not result.get('status') or not result.get('data'):
            return Response(
                {'error': 'Unable to verify payment.', 'details': result},
                status=400
            )

        paystack_status = result['data'].get('status')

        # Only a TERMINAL outcome may change our row. Paystack's non-terminal
        # statuses — 'abandoned' (student closed the checkout) and 'pending' /
        # 'ongoing' / 'processing' (charge not settled yet) — must leave the
        # payment exactly as it is. Paystack raises no `charge.failed` webhook
        # event, so treating "not success (yet)" as failure here was what
        # flipped payments to `failed` that were never actually declined.
        if paystack_status in ('success', 'failed', 'reversed'):
            payment.status = (
                Payment.STATUS_SUCCESS if paystack_status == 'success'
                else Payment.STATUS_FAILED
            )
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

            if payment:
                # Idempotency: a retried webhook must not re-process/overwrite.
                if payment.status == Payment.STATUS_SUCCESS:
                    return Response({'received': True})

                # The paid amount MUST match the expected fee (in kobo).
                # Otherwise a validly-signed webhook for the wrong amount
                # would mark a payment as paid for less than it should be.
                paid_kobo = transaction.get('amount')
                if paid_kobo is None or int(paid_kobo) != int(payment.amount * 100):
                    payment.status = Payment.STATUS_FAILED
                    payment.save()
                    return Response({'received': True})

                payment.status = Payment.STATUS_SUCCESS
                payment.save()

        return Response({'received': True})