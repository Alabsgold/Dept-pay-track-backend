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


class UnverifiedPaymentSerializer(serializers.ModelSerializer):
    """
    Admin refund-review queue (GET /api/payments/unverified/).

    Read-only listing of payments flagged `pending_review` — the money the
    gateway actually took (`paid_amount`) side-by-side with the agreed fee
    (`amount`), so a reviewer can decide a refund without opening the DB.
    Read-only by construction: a reviewer decision happens in the admin, not
    through this listing.
    """

    student_matric = serializers.CharField(
        source='student.matric_number', read_only=True
    )
    student_name = serializers.SerializerMethodField()
    contribution_title = serializers.CharField(
        source='contribution.title',
        read_only=True,
        default=None,  # contribution FK is nullable on legacy rows
    )
    expected_amount = serializers.DecimalField(
        source='contribution.amount',
        max_digits=10,
        decimal_places=2,
        read_only=True,
        default=None,  # null when the contribution FK is null
        coerce_to_string=True,  # money leaves as a string, per contract §3/§4
    )
    amount_received = serializers.DecimalField(
        source='paid_amount',
        max_digits=10,
        decimal_places=2,
        read_only=True,
        default=None,
        coerce_to_string=True,
    )
    mismatch_detail = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            'id',
            'reference',
            'student_matric',
            'student_name',
            'contribution_title',
            'expected_amount',
            'amount_received',
            'mismatch_detail',
            'refund_status',
            'created_at',
        ]

    def get_student_name(self, obj):
        return obj.student.get_full_name() or obj.student.username

    def get_mismatch_detail(self, obj):
        received, expected = obj.paid_amount, None
        if obj.contribution_id:
            expected = obj.contribution.amount
        if received is None or expected is None:
            return 'Amount unverifiable — flagged for review.'
        if received != expected:
            diff = abs(received - expected)
            direction = 'overpaid' if received > expected else 'underpaid'
            return (
                f'Student {direction}: received \u20a6{received:,.2f}; '
                f'expected \u20a6{expected:,.2f} (diff: \u20a6{diff:,.2f}).'
            )
        return 'Amount matches; flagged for other reason.'


class UnverifiedPaymentSerializer(serializers.ModelSerializer):
    """
    Admin refund-review queue (GET /api/payments/unverified/).

    Read-only listing of payments flagged `pending_review` — the money the
    gateway actually took (`paid_amount`) side-by-side with the agreed fee
    (`amount`), so a reviewer can decide a refund without opening the DB.
    Read-only by construction: a reviewer decision happens in the admin, not
    through this listing.
    """

    student_matric = serializers.CharField(
        source='student.matric_number', read_only=True
    )
    student_name = serializers.SerializerMethodField()
    contribution_title = serializers.CharField(
        source='contribution.title',
        read_only=True,
        default=None,  # contribution FK is nullable on legacy rows
    )
    expected_amount = serializers.DecimalField(
        source='contribution.amount',
        max_digits=10,
        decimal_places=2,
        read_only=True,
        default=None,  # null when the contribution FK is null
        coerce_to_string=True,  # money leaves as a string, per contract §3/§4
    )
    amount_received = serializers.DecimalField(
        source='paid_amount',
        max_digits=10,
        decimal_places=2,
        read_only=True,
        default=None,
        coerce_to_string=True,
    )
    mismatch_detail = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            'id',
            'reference',
            'student_matric',
            'student_name',
            'contribution_title',
            'expected_amount',
            'amount_received',
            'mismatch_detail',
            'refund_status',
            'created_at',
        ]

    def get_student_name(self, obj):
        return obj.student.get_full_name() or obj.student.username

    def get_mismatch_detail(self, obj):
        received, expected = obj.paid_amount, None
        if obj.contribution_id:
            expected = obj.contribution.amount
        if received is None or expected is None:
            return 'Amount unverifiable — flagged for review.'
        if received != expected:
            diff = abs(received - expected)
            direction = 'overpaid' if received > expected else 'underpaid'
            return (
                f'Student {direction}: received \u20a6{received:,.2f}; '
                f'expected \u20a6{expected:,.2f} (diff: \u20a6{diff:,.2f}).'
            )
        return 'Amount matches; flagged for other reason.'