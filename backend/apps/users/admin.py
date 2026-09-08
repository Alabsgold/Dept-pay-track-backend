from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import Department, User


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'faculty')
    search_fields = ('name', 'faculty')
    ordering = ('name',)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (
        'id',
        'username',
        'email',
        'matric_number',
        'department',
        'level',
        'role',
        'is_staff',
    )
    list_filter = ('role', 'level', 'department', 'is_staff', 'is_active')
    search_fields = ('username', 'email', 'matric_number', 'phone_number')
    ordering = ('id',)

    fieldsets = BaseUserAdmin.fieldsets + (
        (
            'Student & Role Details',
            {
                'fields': (
                    'matric_number',
                    'department',
                    'level',
                    'role',
                    'phone_number',
                )
            },
        ),
    )

    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        (
            'Student & Role Details',
            {
                'fields': (
                    'email',
                    'matric_number',
                    'department',
                    'level',
                    'role',
                    'phone_number',
                )
            },
        ),
    )
