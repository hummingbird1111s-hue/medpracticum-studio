from django.conf import settings
from django.db import models
from django.utils import timezone


class Notification(models.Model):
    TYPE_INFO    = 'info'
    TYPE_SUCCESS = 'success'
    TYPE_PAYMENT = 'payment'
    TYPE_ADMIN   = 'admin'

    PRIORITY_NORMAL    = 'normal'
    PRIORITY_IMPORTANT = 'important'
    PRIORITY_URGENT    = 'urgent'
    PRIORITY_CHOICES = [
        (PRIORITY_NORMAL,    'Normal'),
        (PRIORITY_IMPORTANT, 'Important'),
        (PRIORITY_URGENT,    'Urgent – action required'),
    ]

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
        'admin_important':   '⚠️',
        'admin_urgent':      '🚨',
        'default':           '🔔',
    }

    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    kind       = models.CharField(max_length=32, default='default')
    # Raised from 500 → 1000 so it matches the 1000-char cap in the admin
    # "Send message" modal (otherwise strict databases reject long messages).
    message    = models.CharField(max_length=1000)
    is_read    = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    sent_by    = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='sent_notifications')

    # How loudly the notification should be shown to the user.
    priority   = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default=PRIORITY_NORMAL, db_index=True)
    # When the user pressed "I understand" on an urgent alert (audit trail for staff).
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def is_urgent(self):
        return self.priority == self.PRIORITY_URGENT

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


def notify(user, message, kind='default', sent_by=None, priority=Notification.PRIORITY_NORMAL):
    """Create a notification. Fails silently if user is None."""
    if user is None:
        return None

    valid = {value for value, _ in Notification.PRIORITY_CHOICES}
    if priority not in valid:
        priority = Notification.PRIORITY_NORMAL

    return Notification.objects.create(
        user=user,
        message=message,
        kind=kind,
        sent_by=sent_by,
        priority=priority,
    )