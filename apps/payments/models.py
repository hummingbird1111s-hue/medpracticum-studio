from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class Subscription(models.Model):
    """
    One subscription per user.

    State machine:
        unsubscribed   → no access (default)
        pending        → user submitted a MoMo reference, waiting for admin
        subscribed     → active access, expires at `expires_at`
        partial        → paid ₵95, first period used, waiting for next period

    Plans:
        single (₵50)   → one practicum period (attachment end + 5 days)
        double (₵95)   → both practicum periods for the level

    Admin controls all transitions via Django admin actions. No automated
    payments — payment is manual Mobile Money, verified by the admin.
    """
    PLAN_SINGLE = 'single'   # ₵50 — one practicum period
    PLAN_DOUBLE = 'double'   # ₵95 — both practicum periods

    STATUS_UNSUBSCRIBED = 'unsubscribed'
    STATUS_PENDING      = 'pending'
    STATUS_SUBSCRIBED   = 'subscribed'
    STATUS_PARTIAL      = 'partial'

    PLAN_CHOICES = [
        (PLAN_SINGLE, 'Single Practicum Period (₵50)'),
        (PLAN_DOUBLE, 'Both Practicum Periods (₵95)'),
    ]
    STATUS_CHOICES = [
        (STATUS_UNSUBSCRIBED, 'Unsubscribed'),
        (STATUS_PENDING,      'Pending verification'),
        (STATUS_SUBSCRIBED,   'Subscribed'),
        (STATUS_PARTIAL,      'Partially subscribed (between periods)'),
    ]

    PRICE_SINGLE = 50
    PRICE_DOUBLE = 95

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='subscription',
    )

    plan = models.CharField(
        max_length=10, choices=PLAN_CHOICES, blank=True,
        help_text='Active plan: single or double.',
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES,
        default=STATUS_UNSUBSCRIBED, db_index=True,
    )

    # Pending submission — what the user typed into the reference form
    pending_plan = models.CharField(max_length=10, blank=True)
    pending_reference = models.CharField(max_length=120, blank=True)
    pending_submitted_at = models.DateTimeField(null=True, blank=True)

    # Active period
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    expires_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Attachment end date + 5-day grace. Access ends here.',
    )
    activated_at = models.DateTimeField(null=True, blank=True)

    # Last confirmed payment (set by admin when approving)
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
        return f'{self.user} — {self.get_status_display()}'

    # ---------------------------------------------------------------- state

    @property
    def is_active(self):
        """Shorthand: status is 'subscribed'. Doesn't check expiry."""
        return self.status == self.STATUS_SUBSCRIBED

    @property
    def is_currently_active(self):
        """True iff status='subscribed' AND not past expires_at."""
        if self.status != self.STATUS_SUBSCRIBED:
            return False
        if self.expires_at and self.expires_at < timezone.now():
            return False
        return True

    @property
    def is_pending(self):
        return self.status == self.STATUS_PENDING

    @property
    def is_partial(self):
        return self.status == self.STATUS_PARTIAL

    @property
    def days_remaining(self):
        if not self.expires_at or self.status != self.STATUS_SUBSCRIBED:
            return 0
        delta = self.expires_at - timezone.now()
        return max(0, delta.days)


# ---------------------------------------------------------------- helpers

def get_subscription(user):
    """Return the user's subscription or None. Never raises."""
    if not user or not user.is_authenticated:
        return None
    try:
        return user.subscription
    except Subscription.DoesNotExist:
        return None


def user_is_subscribed(user):
    """True iff the user has an active, non-expired subscription."""
    sub = get_subscription(user)
    return bool(sub and sub.is_currently_active)


def mark_pending(user, plan, reference):
    """
    Called from the subscribe view when the user submits their MoMo reference.
    Stores the pending state so a page reload still shows the awaiting card.
    Does NOT grant access — admin must approve via Django admin.
    """
    sub, _ = Subscription.objects.get_or_create(user=user)
    sub.status = Subscription.STATUS_PENDING
    sub.pending_plan = plan
    sub.pending_reference = reference
    sub.pending_submitted_at = timezone.now()
    sub.save()
    return sub


def _compute_expiry_for_session(session):
    """
    Return a timezone-aware datetime = max(attachment.end_date, today) + 5 days.
    Fallback: 90 days from now if no attachment.
    """
    from datetime import datetime

    if session and getattr(session, 'end_date', None):
        end = session.end_date
        if isinstance(end, datetime):
            end = end.date()
        base = max(end, timezone.now().date())
        return timezone.make_aware(
            datetime.combine(base + timedelta(days=5), datetime.min.time())
        )
    return timezone.now() + timedelta(days=90)


def activate_subscription(user, plan=Subscription.PLAN_SINGLE, reference='',
                          session=None, expires_at=None):
    """
    Admin action: flip the user to subscribed with the given plan.

    - Computes expires_at from session (attachment end + 5 days) if not provided
    - Records period_start / period_end from the attachment
    - Copies pending_reference into last_reference
    - Clears the pending state
    """
    sub, _ = Subscription.objects.get_or_create(user=user)

    amount = (
        Subscription.PRICE_DOUBLE if plan == Subscription.PLAN_DOUBLE
        else Subscription.PRICE_SINGLE
    )

    sub.status = Subscription.STATUS_SUBSCRIBED
    sub.plan = plan
    sub.activated_at = timezone.now()
    sub.last_amount_paid = amount

    if reference:
        sub.last_reference = reference
    elif sub.pending_reference:
        sub.last_reference = sub.pending_reference

    # Clear pending
    sub.pending_plan = ''
    sub.pending_reference = ''
    sub.pending_submitted_at = None

    if expires_at is not None:
        sub.expires_at = expires_at
    else:
        sub.expires_at = _compute_expiry_for_session(session)

    if session is not None:
        sub.period_start = getattr(session, 'start_date', None)
        sub.period_end = getattr(session, 'end_date', None)

    sub.save()
    return sub


def mark_partial(user):
    """Transition to 'partial' — paid ₵95, first period done, waiting for next."""
    sub, _ = Subscription.objects.get_or_create(user=user)
    sub.status = Subscription.STATUS_PARTIAL
    sub.save()
    return sub


def mark_unsubscribed(user):
    """Transition to 'unsubscribed' — access removed."""
    sub, _ = Subscription.objects.get_or_create(user=user)
    sub.status = Subscription.STATUS_UNSUBSCRIBED
    sub.save()
    return sub