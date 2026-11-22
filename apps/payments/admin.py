from django.contrib import admin

from .models import Subscription


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'is_active', 'activated_at', 'expires_at',
        'last_amount_paid', 'last_reference',
    )
    list_filter = ('is_active',)
    search_fields = ('user__username', 'user__email', 'last_reference')
    readonly_fields = ('created_at', 'updated_at')