from django.contrib import admin

from .models import Payment, Transaction


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
