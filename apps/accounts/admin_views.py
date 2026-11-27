"""
Staff-only admin views: User Logs + All Users + Send Message.

Wired into config/urls.py as:
    /admin-workspace/user-logs/     → admin_user_logs     (name='admin_user_logs')
    /admin-workspace/all-users/     → admin_all_users     (name='admin_all_users')
    /admin-workspace/send-message/  → admin_send_message  (name='admin_send_message')

All three views use @staff_member_required so a non-staff user is bounced
to the admin login page.

All Users page extras
---------------------
Every user row carries live counts: attachments, notifications, logs,
presentations (generated / regenerated), reports (generated / regenerated),
competencies and workshops.

The counts are built from the user's *reverse relations*. Because this file
cannot know your exact `related_name`s, each count lists a few likely names in
COUNT_SOURCES below and the first one that exists on the User model is used.
If none exists, that column shows 0 and a warning is written to the log, so
the page never crashes. To pin a count to a specific relation, put its name
FIRST in the matching tuple.

Messages can be sent as Normal / Important / Urgent. Urgent messages pop up
for the recipient as a full-screen alert they must acknowledge.
"""
import logging

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import (
    Count, F, IntegerField, OuterRef, Q, Subquery, Sum, Value,
)
from django.db.models.functions import Coalesce
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
    {
        'label': '🚨 Urgent — contact admin now',
        'text': (
            'URGENT: please contact the administrator immediately. '
            'Action is required on your account today.'
        ),
    },
]


# ──────────────────────────────────────────────────────────────────────
# Where each per-user count comes from.
# Each tuple = candidate reverse-relation names on User (first match wins).
# ──────────────────────────────────────────────────────────────────────
COUNT_SOURCES = {
    'attachments':   ('attachments', 'attachment_set', 'attachment'),
    'notifications': ('notifications',),
    'logs':          ('logs', 'daily_logs', 'dailylogs', 'log_entries', 'log_set', 'log'),
    'presentations': ('presentations', 'pptx_decks', 'pptx', 'presentation_set', 'presentation',
                      'generated_presentations'),
    'reports':       ('reports', 'generated_reports', 'report_set', 'report'),
    'competencies':  ('competencies', 'competency_set', 'competency'),
    'workshops':     ('workshops', 'workshop_set', 'workshop'),
}

# How "regenerated" is recorded on a presentation / report row.
# A boolean field (True = regenerated) is checked first, then a counter field
# (number of regenerations). If neither exists, everything counts as generated
# and regenerated shows 0.
REGEN_BOOLEAN_FIELDS = ('is_regenerated', 'regenerated', 'is_regeneration', 'is_regen')
REGEN_COUNTER_FIELDS = ('regeneration_count', 'regen_count', 'regenerations', 'times_regenerated')

SORT_OPTIONS = {
    '-attachments_count', '-notifications_count', '-logs_count',
    '-presentations_total', '-reports_total',
    '-competencies_count', '-workshops_count',
}

ALLOWED_PRIORITIES = ('normal', 'important', 'urgent')


def _reverse_relations():
    """Map {query_name: reverse-relation field} for every reverse FK/M2M on User."""
    return {
        f.name: f
        for f in User._meta.get_fields()
        if f.auto_created and not f.concrete and (f.one_to_many or f.many_to_many)
    }


def _find_relation(key, relations):
    for candidate in COUNT_SOURCES[key]:
        if candidate in relations:
            return relations[candidate]
    logger.warning('admin_all_users: no relation found for "%s" (tried %s) — column will show 0',
                   key, ', '.join(COUNT_SOURCES[key]))
    return None


def _zero():
    return Value(0, output_field=IntegerField())


def _subquery_agg(rel, aggregate, **filters):
    """
    Correlated subquery: aggregate over the related rows of the outer user.
    Subqueries (instead of joining every relation at once) keep the query fast
    — joining 8 relations together multiplies rows and gets very slow.
    """
    model = rel.related_model
    fk = rel.field.name
    sub = (
        model.objects
        .filter(**{fk: OuterRef('pk')}, **filters)
        .order_by()
        .values(fk)
        .annotate(_agg=aggregate)
        .values('_agg')
    )
    return Coalesce(Subquery(sub, output_field=IntegerField()), Value(0))


def _count(rel, **filters):
    if rel is None:
        return _zero()
    if rel.one_to_many:
        return _subquery_agg(rel, Count('pk'), **filters)
    # many-to-many fallback
    cond = Q(**{f'{rel.name}__{k}': v for k, v in filters.items()}) if filters else None
    return Count(rel.name, filter=cond, distinct=True)


def _first_field(model, candidates):
    names = {f.name for f in model._meta.get_fields()}
    return next((c for c in candidates if c in names), None)


def _generated_and_regenerated(rel):
    """Return (generated_expr, regenerated_expr) for presentations / reports."""
    if rel is None:
        return _zero(), _zero()

    model = rel.related_model

    flag = _first_field(model, REGEN_BOOLEAN_FIELDS)
    if flag:
        return _count(rel, **{flag: False}), _count(rel, **{flag: True})

    counter = _first_field(model, REGEN_COUNTER_FIELDS)
    if counter and rel.one_to_many:
        return _count(rel), _subquery_agg(rel, Sum(counter))

    return _count(rel), _zero()


def _annotate_counts(qs):
    relations = _reverse_relations()

    attachments   = _find_relation('attachments', relations)
    notifications = _find_relation('notifications', relations)
    logs          = _find_relation('logs', relations)
    presentations = _find_relation('presentations', relations)
    reports       = _find_relation('reports', relations)
    competencies  = _find_relation('competencies', relations)
    workshops     = _find_relation('workshops', relations)

    pres_gen, pres_regen = _generated_and_regenerated(presentations)
    rep_gen, rep_regen   = _generated_and_regenerated(reports)

    return qs.annotate(
        attachments_count=_count(attachments),
        notifications_count=_count(notifications),
        logs_count=_count(logs),
        presentations_generated=pres_gen,
        presentations_regenerated=pres_regen,
        reports_generated=rep_gen,
        reports_regenerated=rep_regen,
        competencies_count=_count(competencies),
        workshops_count=_count(workshops),
    ).annotate(
        presentations_total=F('presentations_generated') + F('presentations_regenerated'),
        reports_total=F('reports_generated') + F('reports_regenerated'),
    )


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
    """Every registered user with profile info, activity counts and filters."""
    qs = _annotate_counts(User.objects.all())

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

    # Sorting: only whitelisted keys; default is alphabetical by username.
    sort = request.GET.get('sort', '').strip()
    if sort in SORT_OPTIONS:
        qs = qs.order_by(sort, 'username')
    else:
        sort = ''
        qs = qs.order_by('username')

    paginator = Paginator(qs, 100)
    page = paginator.get_page(request.GET.get('page'))

    return render(request, 'accounts/admin_all_users.html', {
        'page': page,
        'users': page.object_list,
        'total': paginator.count,
        'current_department': dept,
        'current_role': role,
        'current_sort': sort,
        'q': q,
        'departments': User.DEPARTMENT_CHOICES,
        'message_presets': MESSAGE_PRESETS,
    })


@staff_member_required
@require_POST
def admin_send_message(request):
    """
    Send an admin message to one or more users.

    Each recipient gets a Notification row so the bell icon lights up
    immediately. The message can be Normal, Important or Urgent:

      normal     → kind='admin_message'   (💬, regular toast)
      important  → kind='admin_important' (⚠️, highlighted in the bell panel)
      urgent     → kind='admin_urgent'    (🚨, full-screen alert that must be
                                           acknowledged before it goes away)

    If `notify()` throws for a single user, we log it and keep going for the
    rest — one broken user should never block the whole send.
    """
    from apps.notifications.models import notify  # lazy import — avoid circular

    user_ids = request.POST.getlist('user_ids')
    message_text = (request.POST.get('message') or '').strip()

    priority = (request.POST.get('priority') or 'normal').strip().lower()
    if priority not in ALLOWED_PRIORITIES:
        priority = 'normal'

    if not user_ids:
        messages.error(request, 'No users were selected.')
        return redirect('admin_all_users')

    if not message_text:
        messages.error(request, 'Message cannot be empty.')
        return redirect('admin_all_users')

    # Cap length so a runaway paste can't fill the Notification table with 50KB rows
    if len(message_text) > 1000:
        message_text = message_text[:1000]

    kind = {
        'normal': 'admin_message',
        'important': 'admin_important',
        'urgent': 'admin_urgent',
    }[priority]

    # Only active users — staff can't accidentally wake suspended accounts
    recipients = User.objects.filter(id__in=user_ids, is_active=True)

    sent = 0
    failed = 0
    for user in recipients:
        try:
            notify(
                user,
                message_text,
                kind=kind,
                sent_by=request.user,
                priority=priority,
            )
            sent += 1
        except Exception:
            logger.exception('Failed to send admin message to %s', user.username)
            failed += 1

    label = {'normal': 'Message', 'important': 'Important message', 'urgent': 'URGENT message'}[priority]

    if sent == 0:
        messages.error(request, 'No messages were sent (no active users matched the selection).')
    elif failed:
        messages.warning(
            request,
            f'{label} delivered to {sent} user(s). {failed} failed — check server logs.',
        )
    else:
        messages.success(
            request,
            f'{label} delivered to {sent} user{"s" if sent != 1 else ""}.',
        )

    return redirect('admin_all_users')