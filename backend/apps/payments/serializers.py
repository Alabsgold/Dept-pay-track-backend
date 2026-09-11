from rest_framework import serializers

from .models import Payment


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            'id',
            'student',
            'contribution',
            'payment_type',
            'amount',
            'reference',
            'status',
            'method',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'student',
            'contribution',
            'amount',       # server-side only: comes from the contribution, never the client
            'reference',
            'status',
            'method',
            'created_at',
            'updated_at',
        ]

    def validate_payment_type(self, value):
        valid_types = [
            Payment.PAYMENT_DEPARTMENTAL_FEE,
            Payment.PAYMENT_SPORTS_JERSEY,
            Payment.PAYMENT_EXCURSION,
        ]

        if value not in valid_types:
            raise serializers.ValidationError(
                'Invalid payment type.'
            )

        return value