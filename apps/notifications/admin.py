from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'kind', 'short_message', 'is_read',
        'sent_by', 'created_at',
    )
    list_filter = ('kind', 'is_read', 'created_at')
    search_fields = (
        'user__username', 'user__email',
        'sent_by__username', 'message',
    )
    readonly_fields = ('created_at',)
    date_hierarchy = 'created_at'
    list_select_related = ('user', 'sent_by')
    ordering = ('-created_at',)

    fieldsets = (
        (None, {
            'fields': ('user', 'kind', 'message', 'is_read'),
        }),
        ('Delivery', {
            'fields': ('sent_by', 'created_at'),
        }),
    )

    @admin.display(description='Message')
    def short_message(self, obj):
        text = obj.message or ''
        return text[:70] + ('…' if len(text) > 70 else '')

    actions = ['mark_read', 'mark_unread']

    @admin.action(description='✅ Mark selected as read')
    def mark_read(self, request, queryset):
        updated = queryset.update(is_read=True)
        self.message_user(request, f'{updated} notification(s) marked as read.')

    @admin.action(description='🔵 Mark selected as unread')
    def mark_unread(self, request, queryset):
        updated = queryset.update(is_read=False)
        self.message_user(request, f'{updated} notification(s) marked as unread.')