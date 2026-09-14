from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    contribution_title = serializers.CharField(
        source='contribution.title',
        read_only=True,
        default=None,
    )

    class Meta:
        model = Notification
        # NOTE (contract gap, flagged in directives/BOTTLENECKS.md):
        # API_CONTRACT.md §5 lists the two endpoints but no response shape.
        # The field is named `notification_type` to match the DB doc's
        # "type"; the frontend teammate should confirm before wiring the
        # bell badge.
        fields = [
            'id',
            'notification_type',
            'message',
            'contribution',
            'contribution_title',
            'is_read',
            'created_at',
        ]
        read_only_fields = fields
