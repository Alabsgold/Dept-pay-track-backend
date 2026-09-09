from django.contrib import admin

from .models import Contribution


@admin.register(Contribution)
class ContributionAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'title',
        'department',
        'amount',
        'deadline',
        'is_mandatory',
        'target_level',
        'created_by',
    )
    list_filter = ('department', 'is_mandatory', 'target_level')
    search_fields = ('title', 'description')
    ordering = ('-created_at',)