from django.conf import settings
from django.db import models
from django.utils import timezone


class Notification(models.Model):
    TYPE_INFO    = 'info'
    TYPE_SUCCESS = 'success'
    TYPE_PAYMENT = 'payment'
    TYPE_ADMIN   = 'admin'

    ICONS = {
        'log_added':         '📝',
        'workshop_added':    '🎓',
        'report_generated':  '📊',
        'pptx_generated':    '🖥️',
        'login':             '🔐',
        'logout':            '👋',
        'deletion':          '🗑️',
        'payment_submitted': '💳',
        'admin_message':     '💬',
        'default':           '🔔',
    }

    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    kind       = models.CharField(max_length=32, default='default')
    message    = models.CharField(max_length=500)
    is_read    = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    sent_by    = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='sent_notifications')

    class Meta:
        ordering = ['-created_at']

    @property
    def icon(self):
        return self.ICONS.get(self.kind, self.ICONS['default'])

    @property
    def created_at_display(self):
        delta = timezone.now() - self.created_at
        s = int(delta.total_seconds())
        if s < 60:      return 'just now'
        if s < 3600:    return f'{s // 60}m ago'
        if s < 86400:   return f'{s // 3600}h ago'
        if s < 604800:  return f'{s // 86400}d ago'
        return self.created_at.strftime('%b %d')


def notify(user, message, kind='default', sent_by=None):
    """Create a notification. Fails silently if user is None."""
    if user is None:
        return None
    return Notification.objects.create(user=user, message=message, kind=kind, sent_by=sent_by)