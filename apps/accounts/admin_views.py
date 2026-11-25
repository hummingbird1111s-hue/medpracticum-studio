"""
Staff-only admin views: User Logs + All Users + Send Message.

Wired into config/urls.py as:
    /admin-workspace/user-logs/     → admin_user_logs     (name='admin_user_logs')
    /admin-workspace/all-users/     → admin_all_users     (name='admin_all_users')
    /admin-workspace/send-message/  → admin_send_message  (name='admin_send_message')

All three views use @staff_member_required so a non-staff user is bounced
to the admin login page.
"""
import logging

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .models import User, UserActivityLog

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Preset drafts shown in the "Send message" modal on the All Users page.
# Edit this list to change what staff can quick-pick from.
# ──────────────────────────────────────────────────────────────────────
MESSAGE_PRESETS = [
    {
        'label': 'Reminder — update daily logs',
        'text': (
            'Reminder: please make sure your daily logs for this week are up to date. '
            'The bell icon (top-right) shows anything you may have missed.'
        ),
    },
    {
        'label': 'Welcome message',
        'text': (
            'Welcome to MedPracticum Studio! If you need help getting started, '
            'check the "How to use" page in the menu.'
        ),
    },
    {
        'label': 'Weekly report due',
        'text': (
            'This is a reminder that your weekly report is due. '
            'Please generate and submit it before the end of the day.'
        ),
    },
    {
        'label': 'Profile check',
        'text': (
            'Please review your profile and make sure your department, level, '
            'and contact details are correct.'
        ),
    },
    {
        'label': 'Attachment ending soon',
        'text': (
            'Your current attachment period is nearing its end. '
            'Please make sure all logs and workshops are complete.'
        ),
    },
]


@staff_member_required
def admin_user_logs(request):
    """Show every login / logout / failed-login attempt across the platform."""
    qs = UserActivityLog.objects.select_related('user').order_by('-created_at')

    action = request.GET.get('action', '').strip()
    if action:
        qs = qs.filter(action=action)

    user_q = request.GET.get('user', '').strip()
    if user_q:
        qs = qs.filter(
            Q(user__username__icontains=user_q)
            | Q(user__email__icontains=user_q)
            | Q(user__first_name__icontains=user_q)
            | Q(user__last_name__icontains=user_q)
            | Q(username_attempted__icontains=user_q)
        )

    paginator = Paginator(qs, 100)
    page = paginator.get_page(request.GET.get('page'))

    return render(request, 'accounts/admin_user_logs.html', {
        'page': page,
        'logs': page.object_list,
        'action_choices': UserActivityLog.ACTION_CHOICES,
        'current_action': action,
        'user_query': user_q,
        'total': paginator.count,
    })


@staff_member_required
def admin_all_users(request):
    """Show every registered user with basic info + filter controls."""
    qs = User.objects.all().order_by('-date_joined')

    dept = request.GET.get('department', '').strip()
    if dept:
        qs = qs.filter(department=dept)

    role = request.GET.get('role', '').strip()
    if role == 'staff':
        qs = qs.filter(is_staff=True)
    elif role == 'active':
        qs = qs.filter(is_active=True)
    elif role == 'inactive':
        qs = qs.filter(is_active=False)

    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(username__icontains=q)
            | Q(email__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(index_number__icontains=q)
        )

    paginator = Paginator(qs, 100)
    page = paginator.get_page(request.GET.get('page'))

    return render(request, 'accounts/admin_all_users.html', {
        'page': page,
        'users': page.object_list,
        'total': paginator.count,
        'current_department': dept,
        'current_role': role,
        'q': q,
        'departments': User.DEPARTMENT_CHOICES,
        'message_presets': MESSAGE_PRESETS,
    })


@staff_member_required
@require_POST
def admin_send_message(request):
    """
    Send an admin message to one or more users.

    Each recipient gets a Notification row (kind='admin_message') so the
    bell icon lights up immediately. If `notify()` throws for a single user,
    we log it and keep going for the rest — one broken user should never
    block the whole send.
    """
    from apps.notifications.models import notify  # lazy import — avoid circular

    user_ids = request.POST.getlist('user_ids')
    message_text = (request.POST.get('message') or '').strip()

    if not user_ids:
        messages.error(request, 'No users were selected.')
        return redirect('admin_all_users')

    if not message_text:
        messages.error(request, 'Message cannot be empty.')
        return redirect('admin_all_users')

    # Cap length so a runaway paste can't fill the Notification table with 50KB rows
    if len(message_text) > 1000:
        message_text = message_text[:1000]

    # Only active users — staff can't accidentally wake suspended accounts
    recipients = User.objects.filter(id__in=user_ids, is_active=True)

    sent = 0
    failed = 0
    for user in recipients:
        try:
            notify(user, message_text, kind='admin_message')
            sent += 1
        except Exception:
            logger.exception('Failed to send admin message to %s', user.username)
            failed += 1

    if sent == 0:
        messages.error(request, 'No messages were sent (no active users matched the selection).')
    elif failed:
        messages.warning(
            request,
            f'Message delivered to {sent} user(s). {failed} failed — check server logs.',
        )
    else:
        messages.success(
            request,
            f'Message delivered to {sent} user{"s" if sent != 1 else ""}.',
        )

    return redirect('admin_all_users')