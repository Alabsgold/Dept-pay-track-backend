import requests
import hashlib
import hmac
import json

from django.conf import settings
from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Payment
from .serializers import PaymentSerializer
from .permissions import IsAdminUser


class PaymentListView(generics.ListAPIView):
    serializer_class = PaymentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Payment.objects.filter(student=self.request.user)


class InitializePaymentView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        payment_type = request.data.get('payment_type')
        amount = request.data.get('amount')

        if not payment_type or not amount:
            return Response(
                {'error': 'payment_type and amount are required.'},
                status=400
            )

        valid_types = [
            Payment.PAYMENT_DEPARTMENTAL_FEE,
            Payment.PAYMENT_SPORTS_JERSEY,
            Payment.PAYMENT_EXCURSION,
        ]

        if payment_type not in valid_types:
            return Response(
                {'error': 'Invalid payment type.'},
                status=400
            )

        try:
            amount = float(amount)

            if amount <= 0:
                raise ValueError

        except (ValueError, TypeError):
            return Response(
                {'error': 'Amount must be a valid number greater than zero.'},
                status=400
            )

        amount_in_kobo = int(amount * 100)

        headers = {
            'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}',
            'Content-Type': 'application/json',
        }

        data = {
            'email': request.user.email,
            'amount': amount_in_kobo,
        }

        response = requests.post(
            'https://api.paystack.co/transaction/initialize',
            headers=headers,
            json=data
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
            payment_type=payment_type,
            amount=amount,
            reference=result['data']['reference'],
            status=Payment.STATUS_PENDING,
        )

        return Response({
            'message': 'Payment initialized successfully.',
            'payment': PaymentSerializer(payment).data,
            'authorization_url': result['data']['authorization_url'],
        })


class VerifyPaymentView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, reference):
        url = f'https://api.paystack.co/transaction/verify/{reference}'

        headers = {
            'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}',
        }

        response = requests.get(url, headers=headers)
        result = response.json()

        if not result.get('status'):
            return Response(
                {'error': 'Unable to verify payment.', 'details': result},
                status=400
            )

        payment = Payment.objects.filter(
            reference=reference,
            student=request.user
        ).first()

        if not payment:
            return Response(
                {'error': 'Payment not found.'},
                status=404
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

class AdminPaymentListView(generics.ListAPIView):
    serializer_class = PaymentSerializer
    permission_classes = [IsAdminUser]

    def get_queryset(self):
        return Payment.objects.all()

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
                status=401
            )

        data = json.loads(payload)

        if data.get('event') == 'charge.success':
            transaction = data.get('data', {})
            reference = transaction.get('reference')

            payment = Payment.objects.filter(
                reference=reference
            ).first()

            if payment:
                payment.status = Payment.STATUS_SUCCESS
                payment.save()

        return Response({'message': 'Webhook received.'})