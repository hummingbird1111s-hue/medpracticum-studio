"""
Single source of truth for 'can this user generate a report / deck right now?'
Mirrors the logic in context_processors.py so the sidebar, the subscribe page,
and the generation gate all agree.
"""
from .models import Subscription, get_subscription


def _is_expired_double(sub):
    """Paid for both periods, first expired, not yet reactivated for period 2."""
    return bool(
        sub
        and sub.status == Subscription.STATUS_SUBSCRIBED
        and sub.plan == Subscription.PLAN_DOUBLE
        and not sub.is_currently_active
    )


def subscription_gate(user):
    """
    Returns (allowed, status_key, message).

      allowed    — True only when the user has an active subscription right now.
      status_key — 'subscribed' | 'partial' | 'pending' | 'unsubscribed'
      message    — user-facing explanation for the block ('' when allowed).
    """
    if not user.is_authenticated:
        return False, 'unsubscribed', 'Please log in to generate.'

    try:
        sub = get_subscription(user)
    except Exception:
        return False, 'unsubscribed', 'Could not check your subscription. Please try again.'

    # 1) Active subscription → allowed.
    if sub and sub.is_currently_active:
        return True, 'subscribed', ''

    # 2) Partial — period 1 done, period 2 not yet reactivated.
    if sub and (sub.status == Subscription.STATUS_PARTIAL or _is_expired_double(sub)):
        return False, 'partial', (
            '🌱 Your first practicum period is complete. Generation resumes once you start '
            'your next attachment and the admin reactivates your access. No new payment is needed.'
        )

    # 3) Pending — waiting on admin verification.
    if sub and sub.status == Subscription.STATUS_PENDING:
        return False, 'pending', (
            '⏳ Your payment is pending admin verification. '
            'You will be able to generate as soon as your reference is confirmed.'
        )

    # 4) Unsubscribed.
    return False, 'unsubscribed', (
        '🔒 Generation is a paid feature. Subscribe to unlock reports and presentations.'
    )