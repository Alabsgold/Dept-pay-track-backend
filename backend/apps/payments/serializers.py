from rest_framework import serializers

from .models import Payment


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            'id',
            'student',
            'payment_type',
            'amount',
            'reference',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'student',
            'reference',
            'status',
            'created_at',
            'updated_at',
        ]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                'Amount must be greater than zero.'
            )
        return value

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