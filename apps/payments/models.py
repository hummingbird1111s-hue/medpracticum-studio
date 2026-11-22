from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class Subscription(models.Model):
    """
    One subscription per user. Activated by a successful Paystack payment.
    While `is_currently_active` is True, the user can generate reports and
    presentations without further payment.
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


def user_is_subscribed(user):
    """True iff the user has an active, non-expired subscription."""
    if not user or not user.is_authenticated:
        return False
    try:
        sub = user.subscription
    except Subscription.DoesNotExist:
        return False
    return sub.is_currently_active


def activate_subscription(user, reference='', amount=50, months=6):
    """Mark the user's subscription as active for `months` months."""
    sub, _ = Subscription.objects.get_or_create(user=user)
    sub.is_active = True
    sub.activated_at = timezone.now()
    sub.expires_at = timezone.now() + timedelta(days=30 * months)
    if reference:
        sub.last_reference = reference
    if amount:
        sub.last_amount_paid = amount
    sub.save()
    return sub