from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Notification


def _serialize(n):
    return {
        'id': n.id,
        'message': n.message,
        'icon': n.icon,
        'is_read': n.is_read,
        'priority': n.priority,
        'created_at_display': n.created_at_display,
    }


@login_required
def list_notifications(request):
    # Read-only: fetching the list must NOT mutate read state.
    # Badge count only decreases when the user deletes (single or clear-all)
    # or acknowledges an urgent alert.
    recent = list(request.user.notifications.all()[:50])
    seen_ids = {n.id for n in recent}

    # An unacknowledged urgent alert must never fall off the bottom of the
    # 50-item window, otherwise the user could miss it.
    pending_urgent = request.user.notifications.filter(
        priority=Notification.PRIORITY_URGENT, is_read=False,
    ).exclude(id__in=seen_ids)
    items = list(pending_urgent) + recent

    return JsonResponse({
        'notifications': [_serialize(n) for n in items],
        'unread_count': request.user.notifications.filter(is_read=False).count(),
    })


@login_required
def count_notifications(request):
    return JsonResponse({
        'unread_count': request.user.notifications.filter(is_read=False).count(),
    })


@login_required
@require_POST
def mark_read(request, pk):
    """
    Mark one notification as read. For urgent alerts this is the
    "I understand" acknowledgement — we also stamp acknowledged_at so
    staff have a record of when the user saw it.
    """
    n = Notification.objects.filter(user=request.user, id=pk).first()
    if n is None:
        return JsonResponse({'ok': False, 'error': 'Not found'}, status=404)

    if not n.is_read:
        n.is_read = True
        if n.priority == Notification.PRIORITY_URGENT and n.acknowledged_at is None:
            n.acknowledged_at = timezone.now()
        n.save(update_fields=['is_read', 'acknowledged_at'])

    return JsonResponse({
        'ok': True,
        'unread_count': request.user.notifications.filter(is_read=False).count(),
    })


@login_required
@require_POST
def delete_notification(request, pk):
    # An urgent alert that hasn't been acknowledged can't be deleted away:
    # the user has to press "I understand" first.
    deleted_count, _ = (
        Notification.objects
        .filter(user=request.user, id=pk)
        .exclude(priority=Notification.PRIORITY_URGENT, is_read=False)
        .delete()
    )
    return JsonResponse({
        'ok': True,
        'deleted': deleted_count > 0,
        'unread_count': request.user.notifications.filter(is_read=False).count(),
    })


@login_required
@require_POST
def clear_all(request):
    # Same rule as above: unacknowledged urgent alerts survive "Clear all".
    request.user.notifications.exclude(
        priority=Notification.PRIORITY_URGENT, is_read=False,
    ).delete()
    return JsonResponse({
        'ok': True,
        'unread_count': request.user.notifications.filter(is_read=False).count(),
    })