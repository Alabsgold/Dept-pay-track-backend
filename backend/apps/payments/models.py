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

    # Refund-review states. When the gateway banks money we cannot credit to
    # the fee — a wrong amount, or a fee the student had already paid — the row
    # is failed and the money is FLAGGED for a human to review. Refunds are
    # never issued automatically: moving money back is irreversible, so a
    # person reviews first. 'refunded'/'rejected' are set by that reviewer.
    REFUND_NONE = 'none'
    REFUND_PENDING_REVIEW = 'pending_review'
    REFUND_REFUNDED = 'refunded'
    REFUND_REJECTED = 'rejected'

    REFUND_STATUS_CHOICES = [
        (REFUND_NONE, 'No refund due'),
        (REFUND_PENDING_REVIEW, 'Refund pending review'),
        (REFUND_REFUNDED, 'Refunded'),
        (REFUND_REJECTED, 'Reviewed, no refund due'),
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

    # Offline-payment audit hook: the teller slip / receipt-book number the
    # rep quotes when marking a student paid manually. Empty for online
    # payments — this is what makes manual marks reconcilable with the
    # department's cash book instead of pure trust.
    receipt_reference = models.CharField(
        max_length=50,
        blank=True,
        default='',
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

    # What the gateway actually charged (naira, 2dp). `amount` stays the AGREED
    # fee; this is what really left the student's account, so the pair is the
    # reconciliation record. Null for manual (offline) entries and for rows
    # where the gateway never reported an amount.
    paid_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )

    refund_status = models.CharField(
        max_length=20,
        choices=REFUND_STATUS_CHOICES,
        default=REFUND_NONE,
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.student} - {self.payment_type} - {self.status}"