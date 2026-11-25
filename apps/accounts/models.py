from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    DEPARTMENT_CHOICES = [
        ('HIM', 'Health Information Management'),
        ('PA', 'Physician Assistantship'),
        ('MED_LAB', 'Medical Laboratory Sciences'),
        ('DISEASE_CTRL', 'Disease Control & Epidemiology'),
        ('MENTAL_HEALTH', 'Community Mental Health'),
        ('NUTRITION', 'Community Health Nutrition'),
        ('HEALTH_PROM', 'Health Promotion'),
    ]

    # Override the default email field to enforce uniqueness
    email = models.EmailField(unique=True)

    index_number = models.CharField(max_length=20, unique=True)
    department = models.CharField(max_length=20, choices=DEPARTMENT_CHOICES)
    level = models.IntegerField(default=300)  # 100, 200, 300, 400
    phone_number = models.CharField(max_length=15, blank=True)

    # ===== Password reset via emailed code =====
    password_reset_code = models.CharField(max_length=6, blank=True)
    password_reset_expires_at = models.DateTimeField(null=True, blank=True)
    password_reset_attempts = models.IntegerField(default=0)

    # createsuperuser will ask for these too, so we never get blank duplicates
    REQUIRED_FIELDS = ['email', 'index_number', 'department']

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.index_number})"


# ─────────────────────────────────────────────────────────────
# UserActivityLog — audit trail
#
# Written by apps/notifications/signals.py on the auth signals:
#   user_logged_in     → ACTION_LOGIN
#   user_logged_out    → ACTION_LOGOUT
#   user_login_failed  → ACTION_FAILED (user FK stays null, username captured)
#
# Read by apps/accounts/admin_views.py for the staff User Logs page.
# ─────────────────────────────────────────────────────────────
class UserActivityLog(models.Model):
    ACTION_LOGIN  = 'login'
    ACTION_LOGOUT = 'logout'
    ACTION_FAILED = 'failed_login'

    ACTION_CHOICES = [
        (ACTION_LOGIN,  'Login'),
        (ACTION_LOGOUT, 'Logout'),
        (ACTION_FAILED, 'Failed login'),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='activity_logs',
        help_text='Null for failed-login attempts where no account matched.',
    )
    username_attempted = models.CharField(
        max_length=150, blank=True, default='',
        help_text='Username typed during a failed-login attempt.',
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'User activity log'
        verbose_name_plural = 'User activity logs'
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['action', '-created_at']),
        ]

    def __str__(self):
        who = self.user.username if self.user else (self.username_attempted or 'unknown')
        return f'{who} — {self.action} @ {self.created_at:%Y-%m-%d %H:%M}'