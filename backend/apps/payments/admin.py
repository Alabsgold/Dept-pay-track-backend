from django.contrib import admin

from .models import BMONIWebhookEvent, DepartmentBMONIWallet, Payment, Transaction


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """
    Payments, with the refund-review queue visible.

    Money the gateway banked but we could not credit is failed and flagged
    `refund_status=pending_review`. A human reviews those rows here and then
    refunds through the Paystack dashboard, setting the status to `refunded`
    (or `rejected` when nothing is owed). Refunds are never automatic.
    """

    list_display = (
        'reference',
        'student',
        'contribution',
        'amount',
        'paid_amount',
        'status',
        'refund_status',
        'method',
        'created_at',
    )
    list_filter = ('status', 'refund_status', 'method')
    search_fields = ('reference', 'student__username', 'student__matric_number')
    readonly_fields = ('created_at', 'updated_at')
    date_hierarchy = 'created_at'


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    """
    The raw webhook proof archive (read-only in the admin): what Paystack
    actually said, per reference — the evidence trail behind any refund.
    """

    list_display = ('reference', 'payment', 'created_at')
    search_fields = ('reference',)
    readonly_fields = ('payment', 'reference', 'raw_payload', 'created_at')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False  # rows are written by the webhook, never by hand

    def has_change_permission(self, request, obj=None):
        return False  # proof records must not be edited


@admin.register(DepartmentBMONIWallet)
class DepartmentBMONIWalletAdmin(admin.ModelAdmin):
    """
    One row per department: the NGN account its contributions are paid into.

    The account is issued by BMONI through the API, so the account fields are
    read-only here — editing them by hand would put a number on a student's
    screen that BMONI never issued. `status` and `last_error` stay visible so an
    operator can see what happened. There is no BVN here to leak: only its last
    four digits are stored.
    """

    list_display = (
        'department',
        'account_name',
        'account_number',
        'bank_name',
        'status',
        'provisioned_at',
    )
    list_filter = ('status', 'bank_name')
    search_fields = ('department__name', 'account_name', 'account_number')
    readonly_fields = (
        'bmoni_user_id',
        'bmoni_account_id',
        'account_name',
        'account_number',
        'bank_name',
        'bank_code',
        'currency',
        'provisioned_at',
        'created_at',
        'updated_at',
    )


@admin.register(BMONIWebhookEvent)
class BMONIWebhookEventAdmin(admin.ModelAdmin):
    """
    BMONI's deliveries, verbatim: the evidence trail behind a deposit.

    Read-only in the admin — rows are written by the webhook, never by hand —
    and deduplicated on BMONI's own event id, so a retry never adds a row.
    `processed=False` is the Phase 2 worklist: deliveries are archived without
    crediting them today, so a row here is proof of what BMONI sent, not a claim
    that money was credited.
    """

    list_display = ('event_type', 'event_id', 'wallet', 'processed', 'created_at')
    list_filter = ('event_type', 'processed')
    search_fields = ('event_id', 'event_type')
    readonly_fields = (
        'event_id',
        'event_type',
        'wallet',
        'raw_payload',
        'processed',
        'processed_at',
        'created_at',
    )
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False  # rows are written by the webhook, never by hand

    def has_change_permission(self, request, obj=None):
        return False  # proof records must not be edited
