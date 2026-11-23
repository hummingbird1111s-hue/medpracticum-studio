from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class Subscription(models.Model):
    """
    One subscription per user. Activated by a successful Paystack payment.
    While `is_currently_active` is True, the user can generate reports and
    presentations without further payment.

    Expiry is tied to the practicum period (the user's attachment end date),
    with a 30-day grace period so they can finalize their report after the
    attachment officially ends. Admin bulk-deletes records at semester end.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='subscription',
    )
    is_active = models.BooleanField(default=False)
    activated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_reference = models.CharField(max_length=120, blank=True)
    last_amount_paid = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Subscription'
        verbose_name_plural = 'Subscriptions'

    def __str__(self):
        state = 'active' if self.is_currently_active else 'inactive'
        return f'{self.user} — {state}'

    @property
    def is_currently_active(self):
        if not self.is_active:
            return False
        if self.expires_at and self.expires_at < timezone.now():
            return False
        return True

    @property
    def days_remaining(self):
        if not self.expires_at or not self.is_active:
            return 0
        delta = self.expires_at - timezone.now()
        return max(0, delta.days)


def user_is_subscribed(user):
    """True iff the user has an active, non-expired subscription."""
    if not user or not user.is_authenticated:
        return False
    try:
        sub = user.subscription
    except Subscription.DoesNotExist:
        return False
    return sub.is_currently_active


def activate_subscription(user, reference='', amount=50, expires_at=None,
                          session=None, months=None):
    """
    Activate the user's subscription.

    Priority for the expiry date (first wins):
      1. `expires_at` (explicit datetime)
      2. `session.end_date` (attachment end date) — the normal path
      3. now + `months` * 30 days
      4. now + 90 days (fallback if none of the above are available)

    Grace period: if the computed expiry is in the past (e.g. the attachment
    already ended), extend to 30 days from now so the user can still finalize
    their report. This is intentional — students often need to regenerate
    after their attachment ends.
    """
    sub, _ = Subscription.objects.get_or_create(user=user)
    sub.is_active = True
    sub.activated_at = timezone.now()

    if expires_at is None:
        if session is not None and getattr(session, 'end_date', None):
            expires_at = session.end_date
        elif months is not None:
            expires_at = timezone.now() + timedelta(days=30 * months)
        else:
            expires_at = timezone.now() + timedelta(days=90)

    # 30-day grace period past attachment end (or past "now" if that's later)
    grace = timezone.now() + timedelta(days=30)
    if expires_at < grace:
        expires_at = grace

    sub.expires_at = expires_at
    if reference:
        sub.last_reference = reference
    if amount:
        sub.last_amount_paid = amount
    sub.save()
    return sub