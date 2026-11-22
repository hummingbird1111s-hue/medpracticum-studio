import logging

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


def _months():
    return getattr(settings, 'SUBSCRIPTION_MONTHS', 6)


@login_required
def subscribe(request):
    """Show the subscription page (plan + Paystack checkout placeholder)."""
    subscribed = user_is_subscribed(request.user)
    sub = getattr(request.user, 'subscription', None)
    return render(request, 'payments/subscribe.html', {
        'subscribed': subscribed,
        'subscription': sub,
        'price': _price(),
        'months': _months(),
        'paystack_public_key': getattr(settings, 'PAYSTACK_PUBLIC_KEY', ''),
    })


@login_required
def paystack_callback(request):
    """
    Paystack redirects here after checkout.

    FOR NOW: we trust the `reference` from Paystack and activate the
    subscription. Replace the `activate_subscription` call below with a
    real Paystack `/transaction/verify/{reference}` API call once you
    add your secret key — that way we don't activate on a fake ref.
    """
    reference = (
        request.GET.get('reference')
        or request.GET.get('trxref')
        or ''
    ).strip()
    if not reference:
        messages.error(request, 'Missing payment reference.')
        return redirect('subscribe')

    activate_subscription(
        request.user, reference=reference, amount=_price(), months=_months(),
    )
    messages.success(
        request,
        f'✅ Payment confirmed (ref: {reference}). '
        f'Your subscription is active for the next {_months()} months. '
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

    activate_subscription(
        request.user,
        reference='TEST-' + timezone.now().strftime('%Y%m%d%H%M%S'),
        amount=_price(),
        months=_months(),
    )
    messages.success(
        request,
        f'✅ Test subscription activated for {_months()} months. '
        f'Rebuild your report or presentation to verify access.',
    )
    return redirect('subscribe')