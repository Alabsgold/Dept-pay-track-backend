from django.conf import settings
from django.db import models


class Payment(models.Model):
    PAYMENT_DEPARTMENTAL_FEE = 'departmental_fee'
    PAYMENT_SPORTS_JERSEY = 'sports_jersey'
    PAYMENT_EXCURSION = 'excursion'

    PAYMENT_TYPE_CHOICES = [
        (PAYMENT_DEPARTMENTAL_FEE, 'Departmental Fee'),
        (PAYMENT_SPORTS_JERSEY, 'Sports Jersey'),
        (PAYMENT_EXCURSION, 'Excursion'),
        # Contribution-linked payments (online via Paystack or manually marked).
        # Every such Payment carries a `contribution` FK, which — not this legacy
        # column — is the real fee identity. Legacy choices kept for old rows.
        (PAYMENT_CONTRIBUTION := 'contribution', 'Contribution'),
    ]

    # Payment method: how the money was recorded (online via Paystack, or manual
    # offline entry by a class rep/admin).
    METHOD_ONLINE = 'online'
    METHOD_MANUAL = 'manual'

    METHOD_CHOICES = [
        (METHOD_ONLINE, 'Online'),
        (METHOD_MANUAL, 'Manual'),
    ]

    STATUS_PENDING = 'pending'
    STATUS_SUCCESS = 'success'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_SUCCESS, 'Success'),
        (STATUS_FAILED, 'Failed'),
    ]

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='payments'
    )

    # The fee this payment settles. NOT NULL is deliberately not enforced here:
    # existing rows created online before this field may have null; new rows set
    # it. The contributions bridge uses it to compute has_paid / totals.
    contribution = models.ForeignKey(
        'contributions.Contribution',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payments',
    )

    method = models.CharField(
        max_length=10,
        choices=METHOD_CHOICES,
        default=METHOD_ONLINE,
    )

    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='recorded_payments',
    )

    payment_type = models.CharField(
        max_length=30,
        choices=PAYMENT_TYPE_CHOICES
    )

    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    reference = models.CharField(
        max_length=100,
        unique=True
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.student} - {self.payment_type} - {self.status}"