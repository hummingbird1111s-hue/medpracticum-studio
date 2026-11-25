"""
Staff-only admin views for verifying pending payments.

Wired from config/urls.py as:
    /admin-workspace/payments/              → admin_pending_payments  (name='admin_pending_payments')
    /admin-workspace/payments/verify/<pk>/  → admin_verify_payment    (name='admin_verify_payment')
"""
import logging

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import (
    Subscription,
    activate_subscription,
    get_subscription,
)

logger = logging.getLogger(__name__)


def _get_active_session(user):
    return (
        user.attachments
        .filter(is_deleted=False)
        .order_by('-start_date', '-id')
        .first()
    )


@staff_member_required
def admin_pending_payments(request):
    """List every subscription in 'pending' status, with a one-click verify button."""
    qs = (
        Subscription.objects
        .filter(status=Subscription.STATUS_PENDING)
        .select_related('user')
        .order_by('-pending_submitted_at', '-updated_at')
    )
    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get('page'))

    rows = []
    for sub in page.object_list:
        session = _get_active_session(sub.user)
        plan = sub.pending_plan or Subscription.PLAN_SINGLE
        amount = (
            Subscription.PRICE_DOUBLE if plan == Subscription.PLAN_DOUBLE
            else Subscription.PRICE_SINGLE
        )
        rows.append({
            'sub': sub,
            'session': session,
            'plan': plan,
            'plan_label': (
                'Both Periods' if plan == Subscription.PLAN_DOUBLE
                else 'Single Period'
            ),
            'amount': amount,
        })

    return render(request, 'payments/admin_pending_payments.html', {
        'page': page,
        'rows': rows,
        'total': paginator.count,
    })


@staff_member_required
@require_POST
def admin_verify_payment(request, pk):
    """Activate a pending subscription from the admin workspace."""
    sub = get_object_or_404(Subscription, pk=pk)

    if sub.status != Subscription.STATUS_PENDING:
        messages.warning(
            request,
            f'{sub.user.username} is no longer pending — refresh to see current state.',
        )
        return redirect('admin_pending_payments')

    plan = sub.pending_plan or request.POST.get('plan') or Subscription.PLAN_SINGLE
    if plan not in (Subscription.PLAN_SINGLE, Subscription.PLAN_DOUBLE):
        plan = Subscription.PLAN_SINGLE

    session = _get_active_session(sub.user)
    if not session:
        messages.error(
            request,
            f'{sub.user.username} has no active attachment — expiry cannot be computed. '
            f'Ask them to start an attachment first.',
        )
        return redirect('admin_pending_payments')

    try:
        activate_subscription(
            user=sub.user,
            plan=plan,
            reference=sub.pending_reference,
            session=session,
        )

        # Bell-notify the user so they see the change immediately
        try:
            from apps.notifications.models import notify
            refreshed = get_subscription(sub.user)
            expiry_label = (
                refreshed.expires_at.strftime('%d %b %Y')
                if refreshed and refreshed.expires_at else 'N/A'
            )
            notify(
                sub.user,
                f'✅ Subscription activated — valid until {expiry_label}',
                kind='payment_submitted',
            )
        except Exception:
            logger.exception('Could not notify user %s of activation', sub.user.pk)

        messages.success(
            request,
            f'✅ Verified {sub.user.username} — {plan} plan activated.',
        )
    except Exception:
        logger.exception('Failed to activate subscription for %s', sub.user.pk)
        messages.error(
            request,
            f'Failed to activate {sub.user.username}. Check server logs.',
        )

    return redirect('admin_pending_payments')