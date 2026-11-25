from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import Notification


@login_required
def list_notifications(request):
    qs = request.user.notifications.all()[:50]
    request.user.notifications.filter(is_read=False).update(is_read=True)
    return JsonResponse({
        'notifications': [
            {
                'id': n.id,
                'message': n.message,
                'icon': n.icon,
                'is_read': n.is_read,
                'created_at_display': n.created_at_display,
            } for n in qs
        ],
        'unread_count': 0,
    })


@login_required
def count_notifications(request):
    return JsonResponse({
        'unread_count': request.user.notifications.filter(is_read=False).count(),
    })


@login_required
@require_POST
def delete_notification(request, pk):
    Notification.objects.filter(user=request.user, id=pk).delete()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def clear_all(request):
    request.user.notifications.all().delete()
    return JsonResponse({'ok': True})