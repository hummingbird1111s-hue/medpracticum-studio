import logging
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import activate_subscription, user_is_subscribed

logger = logging.getLogger(__name__)


def _price():
    return getattr(settings, 'SUBSCRIPTION_PRICE', 50)


def _get_active_session(request):
    """Return the user's most recent non-deleted attachment, or None."""
    attachment_id = request.GET.get('attachment_id') or request.POST.get('attachment_id')
    if attachment_id:
        return request.user.attachments.filter(id=attachment_id, is_deleted=False).first()
    return request.user.attachments.filter(is_deleted=False).order_by('-start_date', '-id').first()


def _compute_expected_expiry(session):
    """
    Predicted expiry if the user pays now, tied to their practicum period
    with a 30-day grace so they can finalize the report after the end date.
    """
    if session and getattr(session, 'end_date', None):
        grace_end = timezone.now() + timedelta(days=30)
        # If the attachment already ended, grace_end wins (never expired-on-arrival)
        return max(session.end_date, grace_end)
    # No attachment → 90-day fallback (rare; UI blocks payment for this case)
    return timezone.now() + timedelta(days=90)

@login_required
def pwa_update(request):
    return render(request, 'pwa_update.html')
    
@login_required
def subscribe(request):
    """Show the subscription page (plan + Paystack checkout placeholder)."""
    subscribed = user_is_subscribed(request.user)
    sub = getattr(request.user, 'subscription', None)
    session = _get_active_session(request)
    session_end = getattr(session, 'end_date', None) if session else None
    expected_expires_at = _compute_expected_expiry(session)

    return render(request, 'payments/subscribe.html', {
        'subscribed': subscribed,
        'subscription': sub,
        'price': _price(),
        'session': session,
        'session_end_date': session_end,
        'expected_expires_at': expected_expires_at,
        'days_remaining': sub.days_remaining if sub else 0,
        'paystack_public_key': getattr(settings, 'PAYSTACK_PUBLIC_KEY', ''),
    })


@login_required
def paystack_callback(request):
    """
    Paystack redirects here after checkout.

    FOR NOW: we trust the `reference` from Paystack and activate the
    subscription, tied to the user's active attachment end date. Replace
    the direct `activate_subscription()` call below with a real Paystack
    `/transaction/verify/{reference}` API call once you add your secret
    key — that way we don't activate on a fake ref.
    """
    reference = (
        request.GET.get('reference')
        or request.GET.get('trxref')
        or ''
    ).strip()
    if not reference:
        messages.error(request, 'Missing payment reference.')
        return redirect('subscribe')

    session = _get_active_session(request)
    sub = activate_subscription(
        request.user,
        reference=reference,
        amount=_price(),
        session=session,
    )
    expiry_label = sub.expires_at.strftime('%d %B %Y') if sub.expires_at else 'N/A'
    messages.success(
        request,
        f'✅ Payment confirmed (ref: {reference}). '
        f'Your subscription is active until {expiry_label}. '
        f'You can now generate reports and presentations.',
    )
    return redirect('subscribe')


@login_required
@require_POST
def activate_test(request):
    """
    DEV ONLY — simulate a successful payment so you can test the full flow
    without Paystack wired yet. Restricted to staff, or any user when
    DEBUG=True. Remove this view (or lock to staff only) before launch.
    """
    if not (request.user.is_staff or getattr(settings, 'DEBUG', False)):
        messages.error(request, 'Test activation is disabled.')
        return redirect('subscribe')

    session = _get_active_session(request)
    if session is None:
        messages.error(
            request,
            'Start an attachment first — your subscription is tied to '
            'your practicum period.',
        )
        return redirect('subscribe')

    sub = activate_subscription(
        request.user,
        reference='TEST-' + timezone.now().strftime('%Y%m%d%H%M%S'),
        amount=_price(),
        session=session,
    )
    expiry_label = sub.expires_at.strftime('%d %B %Y') if sub.expires_at else 'N/A'
    messages.success(
        request,
        f'✅ Test subscription activated — valid until {expiry_label}. '
        f'Rebuild your report or presentation to verify access.',
    )
    return redirect('subscribe')