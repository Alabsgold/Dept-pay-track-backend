from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        'recipient',
        'notification_type',
        'is_read',
        'contribution',
        'created_at',
    )
    list_filter = ('notification_type', 'is_read')
    search_fields = ('recipient__username', 'message')
    readonly_fields = ('created_at',)
