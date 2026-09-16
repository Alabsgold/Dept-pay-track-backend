from rest_framework import serializers

from .models import Payment


class PaymentSerializer(serializers.ModelSerializer):
    # Contract §4 (history): `contribution` is the fee's TITLE (never an id —
    # the frontend displays it as-is), and `verified_at` is when the payment
    # reached `success` (null while pending/failed). A null contribution is
    # possible on legacy rows (contribution FK is nullable).
    contribution = serializers.SerializerMethodField()
    verified_at = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            'id',
            'student',
            'contribution',
            'payment_type',
            'amount',
            'paid_amount',
            'reference',
            'status',
            'refund_status',
            'method',
            'created_at',
            'updated_at',
            'verified_at',
        ]
        read_only_fields = [
            'id',
            'student',
            'contribution',
            'amount',       # server-side only: comes from the contribution, never the client
            'paid_amount',  # what the gateway charged — never client-supplied
            'reference',
            'status',
            'refund_status',
            'method',
            'created_at',
            'updated_at',
            'verified_at',
        ]

    def get_contribution(self, obj):
        return obj.contribution.title if obj.contribution_id else None

    def get_verified_at(self, obj):
        return obj.updated_at if obj.status == Payment.STATUS_SUCCESS else None

    def validate_payment_type(self, value):
        valid_types = [
            Payment.PAYMENT_DEPARTMENTAL_FEE,
            Payment.PAYMENT_SPORTS_JERSEY,
            Payment.PAYMENT_EXCURSION,
            Payment.PAYMENT_CONTRIBUTION,
        ]

        if value not in valid_types:
            raise serializers.ValidationError(
                'Invalid payment type.'
            )

        return value