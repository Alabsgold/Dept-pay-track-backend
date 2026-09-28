from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


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
        constraints = [
            # The race-proof: at most ONE credited payment per student per fee.
            # Two concurrent webhooks (e.g. a retry racing the first delivery,
            # or a second reference for the same fee) both pass the app-level
            # `already_credited` check-then-save; this partial unique index is
            # the DB-level backstop that makes double-crediting impossible.
            models.UniqueConstraint(
                fields=['student', 'contribution'],
                # 'success' == Payment.STATUS_SUCCESS (Meta cannot reference
                # the enclosing class's names).
                condition=Q(status='success'),
                name='unique_success_per_student_fee',
            ),
        ]

    def __str__(self):
        return f"{self.student} - {self.payment_type} - {self.status}"


class Transaction(models.Model):
    """
    Proof record for one verified Paystack webhook (charge.success).

    The repo's own spec (AGENTS.md / BACKEND_DB_STRUCTURE.md) calls for a raw
    payload audit trail — if a refund is ever disputed, this is the evidence
    of exactly what the gateway said, when. The FIRST delivery for a reference
    is stored (get_or_create semantics in the webhook); Paystack's identical
    retries are not duplicated. `payment` is nullable: an event for a
    reference we don't recognise is still worth keeping on file.
    """

    payment = models.ForeignKey(
        Payment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='transactions',
    )

    reference = models.CharField(max_length=100, db_index=True)

    # Exactly what Paystack POSTed (verified signature), untouched.
    raw_payload = models.JSONField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.reference} ({self.created_at:%Y-%m-%d %H:%M} UTC)"


class DepartmentBMONIWallet(models.Model):
    """
    The department's own NGN virtual bank account (BMONI Embedded).

    One row per department. This is the account a department publishes for
    bank-transfer contributions — the money physically lands in it — which is
    why the account details are stored rather than fetched per request: a
    student's payment screen must not depend on BMONI being reachable.

    The account is issued by BMONI for a real, BVN-verified person, the
    department's nominated account holder. `account_name` comes back from BMONI
    already formatted and is what a contributor must see (it is the name their
    own bank app will show them); the `holder_*` fields are what we sent.

    The BVN is deliberately NOT stored. It is a sensitive national identifier,
    so only its last four digits are kept, purely so an operator can confirm the
    right person is on the account. A retry after a failure re-asks for the BVN
    instead of leaving it in the database.

    `status` is the provisioning state, not a money state:
      pending -> a row exists but no account has been issued yet
      active  -> BMONI issued the account; the fields below are BMONI's own
      failed  -> the last attempt failed and `last_error` says why (sanitized)
    """

    STATUS_PENDING = 'pending'
    STATUS_ACTIVE = 'active'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_ACTIVE, 'Active'),
        (STATUS_FAILED, 'Failed'),
    ]

    department = models.OneToOneField(
        'users.Department',
        on_delete=models.CASCADE,
        related_name='bmoni_wallet',
    )

    # The nominated account holder, as sent to BMONI. Kept so a retry — or the
    # recovery after a duplicate-create — can identify the SAME person instead
    # of opening a second one.
    holder_first_name = models.CharField(max_length=100, blank=True, default='')
    holder_last_name = models.CharField(max_length=100, blank=True, default='')
    holder_email = models.EmailField(blank=True, default='')
    holder_phone = models.CharField(max_length=20, blank=True, default='')
    # Last four digits only — the full BVN is never stored (see the docstring).
    holder_bvn_last4 = models.CharField(max_length=4, blank=True, default='')

    # BMONI's identifiers. `bmoni_user_id` is unique so the DB — not just the
    # view's read-before-write — guarantees one BMONI person per account.
    bmoni_user_id = models.CharField(
        max_length=64, unique=True, null=True, blank=True
    )
    bmoni_account_id = models.CharField(max_length=64, blank=True, default='')

    # The issued account, exactly as BMONI reports it.
    account_name = models.CharField(max_length=150, blank=True, default='')
    account_number = models.CharField(max_length=20, blank=True, default='')
    bank_name = models.CharField(max_length=100, blank=True, default='')
    bank_code = models.CharField(max_length=20, blank=True, default='')
    currency = models.CharField(max_length=3, default='NGN')

    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    # Why the last attempt failed, already sanitized for display: never the API
    # key, never the BVN, never an upstream body we have not interpreted.
    last_error = models.CharField(max_length=255, blank=True, default='')

    provisioned_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # An `active` wallet with no account number would put a broken
            # "pay here" card in front of students; the DB refuses that state.
            models.CheckConstraint(
                condition=~Q(status='active') | ~Q(account_number=''),
                name='bmoni_active_wallet_has_account_number',
            ),
        ]

    def __str__(self):
        if self.status == self.STATUS_ACTIVE:
            return f"{self.department} NGN account {self.account_number}"
        return f"{self.department} NGN account ({self.status})"

    def mark_active(self, account, bmoni_user_id=None):
        """
        Store a BMONI-issued account (caller saves).

        `account` is BMONI's own account object, read unmodified: we record what
        they issued rather than what we hoped for, so the number shown to
        students is always the number BMONI actually created.
        """
        if bmoni_user_id:
            self.bmoni_user_id = bmoni_user_id
        self.bmoni_account_id = account.get('id') or ''
        self.account_name = account.get('accountName') or ''
        self.account_number = account.get('accountNumber') or ''
        self.bank_name = account.get('bankName') or ''
        self.bank_code = account.get('bankCode') or ''
        self.currency = account.get('currency') or 'NGN'
        self.status = self.STATUS_ACTIVE
        self.last_error = ''
        self.provisioned_at = timezone.now()

    def mark_failed(self, message):
        """Record why provisioning failed (caller saves). Display-safe only."""
        self.status = self.STATUS_FAILED
        self.last_error = (message or '')[:255]


class BMONIWebhookEvent(models.Model):
    """
    One BMONI delivery, kept verbatim.

    Same job `Transaction` does for Paystack: if a deposit is ever disputed,
    this is the evidence of exactly what BMONI said and when. BMONI retries
    deliveries, so `event_id` — their `x-webhook-event-id` header — is unique
    and a repeat delivery is a no-op. A duplicate can never be counted twice.

    `processed` is the crediting worklist. Phase 1 archives deliveries without
    crediting them: crediting a deposit needs our own partner key and sandbox
    tokens (see docs/BMONI_SANDBOX_RUNBOOK.md), so a row here is proof of what
    BMONI sent — not a claim that any money was credited.
    """

    event_id = models.CharField(max_length=128, unique=True)
    event_type = models.CharField(
        max_length=100, blank=True, default='', db_index=True
    )

    # The department's account this event is about, when we can tell. Nullable:
    # an event for something we do not recognise is still worth keeping on file.
    wallet = models.ForeignKey(
        DepartmentBMONIWallet,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='webhook_events',
    )

    # Exactly what BMONI POSTed (signature verified before this row exists).
    raw_payload = models.JSONField()

    processed = models.BooleanField(default=False, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.event_type or 'unknown'} ({self.event_id})"