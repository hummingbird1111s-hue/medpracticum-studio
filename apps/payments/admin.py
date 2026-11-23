from django.contrib import admin
from django.utils import timezone

from .models import Subscription


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'is_active', 'activated_at', 'expires_at',
        'days_remaining', 'last_amount_paid', 'last_reference',
    )
    list_filter = ('is_active',)
    search_fields = ('user__username', 'user__email', 'last_reference')
    readonly_fields = ('created_at', 'updated_at', 'days_remaining')
    date_hierarchy = 'created_at'
    actions = ['deactivate_selected', 'delete_selected']

    fieldsets = (
        (None, {
            'fields': ('user', 'is_active', 'activated_at', 'expires_at')
        }),
        ('Payment details', {
            'fields': ('last_reference', 'last_amount_paid'),
            'classes': ('collapse',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'days_remaining'),
            'classes': ('collapse',),
        }),
    )

    @admin.display(description='Days left')
    def days_remaining(self, obj):
        return obj.days_remaining

    @admin.action(description='Deactivate selected (keep records for audit)')
    def deactivate_selected(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(
            request,
            f'{updated} subscription(s) deactivated. '
            f'They will no longer grant report/presentation access.',
        )

    @admin.action(description='Delete selected subscriptions permanently')
    def delete_selected(self, request, queryset):
        count = queryset.count()
        queryset.delete()
        self.message_user(
            request,
            f'{count} subscription(s) deleted permanently. '
            f'Students must pay again to regain access.',
        )