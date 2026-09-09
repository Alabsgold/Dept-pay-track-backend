from decimal import Decimal

from rest_framework import serializers

from .models import Contribution


class ContributionSerializer(serializers.ModelSerializer):
    amount = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal('0.01'),
    )
    deadline = serializers.DateTimeField(allow_null=True, required=True)
    target_level = serializers.ChoiceField(
        choices=Contribution._meta.get_field('target_level').choices,
        allow_null=True,
        required=False,
    )
    has_paid = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Contribution
        # Response shape matches API_CONTRACT.md exactly:
        # { id, title, amount, deadline, is_mandatory, target_level, has_paid }.
        # `description` is accepted on create but deliberately not returned
        # (contract shows the same shape for GET and POST 201 responses).
        fields = [
            'id',
            'title',
            'description',
            'amount',
            'deadline',
            'is_mandatory',
            'target_level',
            'has_paid',
        ]
        read_only_fields = ['id', 'has_paid']
        extra_kwargs = {
            'description': {'write_only': True, 'required': False},
        }

    def validate_amount(self, value):
        # Money rule: keep Decimal end-to-end; never float (BRIEF.md).
        if value <= 0:
            raise serializers.ValidationError('Amount must be greater than zero.')
        return value

    def get_has_paid(self, obj):
        request = self.context.get('request')
        user = getattr(request, 'user', None) if request is not None else None
        if user is None or not getattr(user, 'is_authenticated', False):
            return False
        return obj.has_paid(user)