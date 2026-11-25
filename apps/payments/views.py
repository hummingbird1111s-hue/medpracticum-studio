import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    Subscription,
    activate_subscription,
    get_subscription,
    mark_pending,
    user_is_subscribed,
)

logger = logging.getLogger(__name__)


def _get_active_session(request):
    """Return the user's most recent non-deleted attachment, or None."""
    attachment_id = request.GET.get('attachment_id') or request.POST.get('attachment_id')
    if attachment_id:
        return request.user.attachments.filter(id=attachment_id, is_deleted=False).first()
    return request.user.attachments.filter(is_deleted=False).order_by('-start_date', '-id').first()


def _compute_expected_expiry(session):
    """Predicted expiry if the user pays now — attachment end + 5 days, floored at today + 5."""
    from datetime import datetime, timedelta

    if session and getattr(session, 'end_date', None):
        end = session.end_date
        if isinstance(end, datetime):
            end = end.date()
        base_date = max(end, timezone.now().date())
        return base_date + timedelta(days=5)

    return (timezone.now() + timedelta(days=90)).date()


def _is_expired_double_plan(sub):
    """
    True if the user paid for the DOUBLE plan, was activated, and period 1's
    expires_at has now passed — but they have NOT yet been reactivated for
    period 2 (or marked unsubscribed by admin).

    Mirrors the auto-detect branch in context_processors.py so the sidebar
    badge and the subscribe page always agree.
    """
    return bool(
        sub
        and sub.status == Subscription.STATUS_SUBSCRIBED
        and sub.plan == Subscription.PLAN_DOUBLE
        and not sub.is_currently_active
    )


def _is_partial_state(sub):
    """
    True if the user should be shown the 'partial' state ('You're covered for
    your next practicum'). Covers both:
      • explicit partial — admin ran the mark_partial action
      • auto-detected expired-double — first period ended, second already paid for
    """
    return bool(
        sub
        and (
            sub.status == Subscription.STATUS_PARTIAL
            or _is_expired_double_plan(sub)
        )
    )


@login_required
def pwa_update(request):
    return render(request, 'pwa_update.html')


@login_required
def subscribe(request):
    """
    Subscription page.

    GET  — shows plans + MoMo instructions + reference form
    POST — receives `notify_only=1` from the JS (best-effort persistence of
           the pending state so a reload shows the awaiting card).
    """
    # ---- Best-effort POST: persist the pending state ----
    if request.method == 'POST' and request.POST.get('notify_only') == '1':
        from apps.notifications.models import notify
        from apps.accounts.models import User

        plan = (request.POST.get('plan') or '').strip()
        ref = (request.POST.get('reference') or '').strip()

        if plan not in (Subscription.PLAN_SINGLE, Subscription.PLAN_DOUBLE) or not ref:
            return JsonResponse({'ok': False, 'error': 'invalid plan or reference'}, status=400)

        try:
            existing = get_subscription(request.user)

            # Never downgrade an already-active subscription via this path.
            if existing and existing.is_currently_active:
                return JsonResponse({'ok': False, 'error': 'already subscribed'}, status=400)

            # Never let a partial user overwrite their paid period-2 privilege
            # by re-submitting. The page hides the form in STATE B; this guard
            # catches stale tabs and DevTools bypasses.
            if _is_partial_state(existing):
                return JsonResponse({'ok': False, 'error': 'already covered'}, status=400)

            old_ref = (
                existing.pending_reference
                if existing and existing.status == Subscription.STATUS_PENDING
                else ''
            )
            mark_pending(request.user, plan, ref)
            ref_changed = (ref != old_ref)

            if ref_changed:
                # 1) Acknowledge to the submitter
                notify(
                    request.user,
                    f'Payment reference received ({ref}) — awaiting verification',
                    kind='payment_submitted',
                )

                # 2) Alert every staff member except the submitter
                amount_label = '₵95' if plan == Subscription.PLAN_DOUBLE else '₵50'
                plan_label = 'Both Periods' if plan == Subscription.PLAN_DOUBLE else 'Single Period'
                staff_msg = (
                    f'💳 {request.user.username} submitted a payment · '
                    f'{plan_label} · {amount_label} · ref {ref}'
                )
                for staff in User.objects.filter(is_staff=True, is_active=True).exclude(pk=request.user.pk):
                    try:
                        notify(staff, staff_msg, kind='payment_submitted')
                    except Exception:
                        logger.exception('Failed to notify staff %s of pending payment', staff.pk)

        except Exception:
            logger.exception('Failed to persist pending subscription for %s', request.user)
            return JsonResponse({'ok': False, 'error': 'server error'}, status=500)

        return JsonResponse({'ok': True})

    sub = get_subscription(request.user)
    subscribed = bool(sub and sub.is_currently_active)
    pending = bool(sub and sub.status == Subscription.STATUS_PENDING)
    partially = _is_partial_state(sub)  # ← the fix

    session = _get_active_session(request)
    session_end = getattr(session, 'end_date', None) if session else None
    expected_expires_at = _compute_expected_expiry(session)

    return render(request, 'payments/subscribe.html', {
        'subscribed': subscribed,
        'partially_subscribed': partially,
        'payment_pending': pending,
        'pending_reference': sub.pending_reference if pending else '',
        'subscription': sub,
        'subscription_plan': sub.plan if sub else '',
        'price_single': Subscription.PRICE_SINGLE,
        'price_double': Subscription.PRICE_DOUBLE,
        'session': session,
        'session_end_date': session_end,
        'expected_expires_at': expected_expires_at,
        'days_remaining': sub.days_remaining if sub else 0,
    })


@login_required
@require_POST
def activate_test(request):
    """DEV ONLY — simulate a successful payment. Staff-only in production."""
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

    plan = (request.POST.get('plan') or Subscription.PLAN_SINGLE).strip()
    if plan not in (Subscription.PLAN_SINGLE, Subscription.PLAN_DOUBLE):
        plan = Subscription.PLAN_SINGLE

    sub = activate_subscription(
        request.user,
        plan=plan,
        reference='TEST-' + timezone.now().strftime('%Y%m%d%H%M%S'),
        session=session,
    )
    expiry_label = sub.expires_at.strftime('%d %B %Y') if sub.expires_at else 'N/A'

    # Bell notification so the user sees the change without reloading
    try:
        from apps.notifications.models import notify
        notify(
            request.user,
            f'✅ Test subscription activated ({plan}) — valid until {expiry_label}',
            kind='payment_submitted',
        )
    except Exception:
        logger.exception('Failed to notify user of test activation')

    messages.success(
        request,
        f'✅ Test subscription activated ({plan}) — valid until {expiry_label}. '
        f'Rebuild your report or presentation to verify access.',
    )
    return redirect('subscribe')