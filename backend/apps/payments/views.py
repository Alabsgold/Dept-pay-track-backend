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

        result = response.json()

        if not result.get('status'):
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
            reference=result['data']['reference'],
            status=Payment.STATUS_PENDING,
            method=Payment.METHOD_ONLINE,
        )

        return Response({
            'message': 'Payment initialized successfully.',
            'payment': PaymentSerializer(payment).data,
            'authorization_url': result['data']['authorization_url'],
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
        result = response.json()

        if not result.get('status'):
            return Response(
                {'error': 'Unable to verify payment.', 'details': result},
                status=400
            )

        paystack_status = result['data']['status']

        if paystack_status == 'success':
            payment.status = Payment.STATUS_SUCCESS
        else:
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

        data = json.loads(payload)

        if data.get('event') == 'charge.success':
            transaction = data.get('data', {})
            reference = transaction.get('reference')

            payment = Payment.objects.filter(
                reference=reference
            ).first()

            if payment:
                # Idempotency: a retried webhook must not re-process/overwrite.
                if payment.status == Payment.STATUS_SUCCESS:
                    return Response({'message': 'Webhook received.'})

                # The paid amount MUST match the expected fee (in kobo).
                # Otherwise a validly-signed webhook for the wrong amount
                # would mark a payment as paid for less than it should be.
                paid_kobo = transaction.get('amount')
                if paid_kobo is None or int(paid_kobo) != int(payment.amount * 100):
                    payment.status = Payment.STATUS_FAILED
                    payment.save()
                    return Response({'message': 'Webhook received.'})

                payment.status = Payment.STATUS_SUCCESS
                payment.save()

        return Response({'message': 'Webhook received.'})