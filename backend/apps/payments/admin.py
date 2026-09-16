from django.contrib import admin

from .models import Payment


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
