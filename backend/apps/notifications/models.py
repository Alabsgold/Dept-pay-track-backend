from django.conf import settings
from django.db import models


class Notification(models.Model):
    """
    A system-generated alert for one user (the frontend bell-icon dropdown).

    Contract (API_CONTRACT.md §5): there is deliberately NO create endpoint.
    Rows are only produced by the signal handlers in signals.py, on:
      * a Payment transitioning to success/failed (the webhook, the verify
        endpoint and the manual mark-paid bridge all funnel through
        Payment.save), and
      * the creation of a new Contribution.
    """

    TYPE_PAYMENT_SUCCESS = 'payment_success'
    TYPE_PAYMENT_FAILED = 'payment_failed'
    TYPE_NEW_CONTRIBUTION = 'new_contribution'

    TYPE_CHOICES = [
        (TYPE_PAYMENT_SUCCESS, 'Payment Successful'),
        (TYPE_PAYMENT_FAILED, 'Payment Failed'),
        (TYPE_NEW_CONTRIBUTION, 'New Contribution'),
    ]

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    notification_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    message = models.CharField(max_length=255)
    # Optional deep-link target so the frontend can jump to the fee the alert
    # is about. SET_NULL keeps the history if a contribution is ever deleted.
    contribution = models.ForeignKey(
        'contributions.Contribution',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications',
    )
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            # The unread-badge query: recipient's unread count.
            models.Index(fields=['recipient', 'is_read']),
        ]
        verbose_name = 'Notification'
        verbose_name_plural = 'Notifications'

    def __str__(self):
        return f"{self.recipient} - {self.notification_type} - read={self.is_read}"
