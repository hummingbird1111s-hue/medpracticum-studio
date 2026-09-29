from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('Student Info', {'fields': ('index_number', 'department', 'level', 'phone_number')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Student Info', {'fields': ('index_number', 'department', 'level', 'phone_number')}),
    )
    list_display = ('username', 'index_number', 'department', 'level', 'is_staff')