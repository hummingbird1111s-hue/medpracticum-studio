"""
Staff-only admin views: User Logs + All Users + Send Message.

Wired into config/urls.py as:
    /admin-workspace/user-logs/     → admin_user_logs
    /admin-workspace/all-users/     → admin_all_users
    /admin-workspace/send-message/  → admin_send_message
"""
import logging
from importlib import import_module

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import FieldDoesNotExist
from django.core.paginator import Paginator
from django.db import models as django_models
from django.db.models import (
    Count, F, IntegerField, OuterRef, Q, Subquery, Sum, Value,
)
from django.db.models.functions import Coalesce
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .models import User, UserActivityLog

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Presets — unchanged
# ──────────────────────────────────────────────────────────────────────
MESSAGE_PRESETS = [
    {'label': 'Reminder — update daily logs',
     'text': ('Reminder: please make sure your daily logs for this week are up to date. '
              'The bell icon (top-right) shows anything you may have missed.')},
    {'label': 'Welcome message',
     'text': ('Welcome to MedPracticum Studio! If you need help getting started, '
              'check the "How to use" page in the menu.')},
    {'label': 'Weekly report due',
     'text': ('This is a reminder that your weekly report is due. '
              'Please generate and submit it before the end of the day.')},
    {'label': 'Profile check',
     'text': ('Please review your profile and make sure your department, level, '
              'and contact details are correct.')},
    {'label': 'Attachment ending soon',
     'text': ('Your current attachment period is nearing its end. '
              'Please make sure all logs and workshops are complete.')},
    {'label': '🚨 Urgent — contact admin now',
     'text': ('URGENT: please contact the administrator immediately. '
              'Action is required on your account today.')},
]


# ──────────────────────────────────────────────────────────────────────
# Direct reverse relations on User (still work without joins)
# ──────────────────────────────────────────────────────────────────────
COUNT_SOURCES = {
    'attachments':   ('attachments', 'attachment_set', 'attachment'),
    'notifications': ('notifications', 'sent_notifications'),
}


SORT_OPTIONS = {
    '-attachments_count', '-notifications_count', '-logs_count',
    '-presentations_total', '-reports_total',
    '-competencies_count', '-workshops_count',
}

ALLOWED_PRIORITIES = ('normal', 'important', 'urgent')


# ──────────────────────────────────────────────────────────────────────
# Resolve AttachmentSession + its FK to User once at import time
# ──────────────────────────────────────────────────────────────────────
def _get_attachment_model():
    try:
        return User._meta.get_field('attachments').related_model
    except (FieldDoesNotExist, AttributeError):
        return None


AttachmentSession = _get_attachment_model()


def _attachment_user_fk():
    """Field on AttachmentSession that points at User (e.g. 'user')."""
    if AttachmentSession is None:
        return None
    for f in AttachmentSession._meta.get_fields():
        if f.is_relation and (f.many_to_one or f.one_to_one) and f.related_model is User:
            return f.name
    return None


def _zero():
    return Value(0, output_field=IntegerField())


def _has_field(model, field_name):
    """
    True only for CONCRETE model fields — excludes reverse relations and M2M.
    This prevents the class of bug where _has_field returns True for a reverse
    FK and then the code tries to filter on it as if it were a JSON/scalar field.
    """
    try:
        field = model._meta.get_field(field_name)
        return bool(getattr(field, 'concrete', False))
    except (FieldDoesNotExist, AttributeError):
        return False


def _try_import_models(module_path, names):
    """Return the first model that exists in module_path, or None."""
    try:
        mod = import_module(module_path)
    except ImportError:
        return None
    for name in names:
        model = getattr(mod, name, None)
        if model is not None and hasattr(model, '_meta'):
            return model
    return None


def _find_fk_to_attachment(model):
    """FK or OneToOne field on model that points at AttachmentSession."""
    if AttachmentSession is None:
        return None
    for f in model._meta.get_fields():
        if f.is_relation and (f.many_to_one or f.one_to_one) and f.related_model is AttachmentSession:
            return f.name
    return None


# ──────────────────────────────────────────────────────────────────────
# Direct counts (user.attachments, user.notifications)
# ──────────────────────────────────────────────────────────────────────
def _reverse_relations():
    return {
        f.name: f
        for f in User._meta.get_fields()
        if f.auto_created and not f.concrete and (f.one_to_many or f.many_to_many)
    }


def _find_relation(key, relations):
    for candidate in COUNT_SOURCES.get(key, ()):
        if candidate in relations:
            return relations[candidate]
    return None


def _count_direct(rel):
    if rel is None:
        return _zero()
    model = rel.related_model
    fk = rel.field.name
    sub = (
        model.objects
        .filter(**{fk: OuterRef('pk')})
        .order_by()
        .values(fk)
        .annotate(_agg=Count('pk'))
        .values('_agg')
    )
    return Coalesce(Subquery(sub, output_field=IntegerField()), Value(0))


# ──────────────────────────────────────────────────────────────────────
# Transitive counts (via AttachmentSession)
# ──────────────────────────────────────────────────────────────────────
def _count_via_attachment(model, attachment_fk_name, *, non_empty_fields=None):
    user_fk = _attachment_user_fk()
    if not user_fk or not attachment_fk_name:
        return _zero()

    qs = model.objects.filter(**{f'{attachment_fk_name}__{user_fk}': OuterRef('pk')})

    for field_name in (non_empty_fields or ()):
        if not _has_field(model, field_name):
            continue
        field = model._meta.get_field(field_name)
        if isinstance(field, django_models.JSONField):
            qs = qs.exclude(**{f'{field_name}__isnull': True}).exclude(**{field_name: {}})
        else:
            qs = qs.exclude(**{f'{field_name}__isnull': True}).exclude(**{field_name: ''})

    sub = (
        qs.order_by()
        .values(f'{attachment_fk_name}__{user_fk}')
        .annotate(_agg=Count('pk'))
        .values('_agg')
    )
    return Coalesce(Subquery(sub, output_field=IntegerField()), Value(0))


def _count_logs():
    DailyLog = _try_import_models('apps.logs.models', ('DailyLog', 'LogEntry', 'Log'))
    if DailyLog is None:
        return _zero()
    return _count_via_attachment(DailyLog, _find_fk_to_attachment(DailyLog))


def _count_workshops():
    Workshop = _try_import_models('apps.logs.models', ('Workshop',))
    if Workshop is None:
        return _zero()
    return _count_via_attachment(Workshop, _find_fk_to_attachment(Workshop))


def _count_competencies():
    """
    ✅ FIXED: AttachmentSession.competencies is a reverse FK to
    AttachmentCompetency — NOT a JSON field. Count the rows of that model
    that belong to the user's attachments.
    """
    Competency = _try_import_models(
        'apps.logs.models',
        ('AttachmentCompetency', 'Competency', 'CompetencyArea', 'AttachmentCompetencyArea'),
    )
    if Competency is None:
        logger.warning(
            'admin_all_users: cannot find AttachmentCompetency model in apps.logs.models '
            '— competencies column will show 0'
        )
        return _zero()

    fk = _find_fk_to_attachment(Competency)
    if not fk:
        logger.warning(
            'admin_all_users: %s has no FK to AttachmentSession — competencies column will show 0',
            Competency.__name__,
        )
        return _zero()

    return _count_via_attachment(Competency, fk)


def _count_reports_generated():
    Gen = _try_import_models('apps.reports.models', ('GeneratedReport',))
    if Gen is None:
        return _zero()
    fk = _find_fk_to_attachment(Gen)
    return _count_via_attachment(Gen, fk, non_empty_fields=('content',))


def _count_presentations_generated():
    Gen = _try_import_models('apps.reports.models', ('GeneratedReport',))
    if Gen is None:
        return _zero()
    fk = _find_fk_to_attachment(Gen)
    return _count_via_attachment(Gen, fk, non_empty_fields=('pptx_content',))


def _sum_regenerations(counter_field):
    """
    Sum `counter_field` on every GeneratedReport whose attachment belongs to
    the outer user. Returns 0 if the field doesn't exist yet (pre-migration).
    """
    Gen = _try_import_models('apps.reports.models', ('GeneratedReport',))
    if Gen is None or not _has_field(Gen, counter_field):
        return _zero()

    user_fk = _attachment_user_fk()
    if not user_fk:
        return _zero()

    fk = _find_fk_to_attachment(Gen)
    if not fk:
        return _zero()

    sub = (
        Gen.objects
        .filter(**{f'{fk}__{user_fk}': OuterRef('pk')})
        .order_by()
        .values(f'{fk}__{user_fk}')
        .annotate(_agg=Sum(counter_field))
        .values('_agg')
    )
    return Coalesce(Subquery(sub, output_field=IntegerField()), Value(0))


# ──────────────────────────────────────────────────────────────────────
# Main annotation
# ──────────────────────────────────────────────────────────────────────
def _annotate_counts(qs):
    relations = _reverse_relations()

    return qs.annotate(
        attachments_count=_count_direct(_find_relation('attachments', relations)),
        notifications_count=_count_direct(_find_relation('notifications', relations)),
        logs_count=_count_logs(),
        presentations_generated=_count_presentations_generated(),
        presentations_regenerated=_sum_regenerations('pptx_regeneration_count'),
        reports_generated=_count_reports_generated(),
        reports_regenerated=_sum_regenerations('regeneration_count'),
        competencies_count=_count_competencies(),
        workshops_count=_count_workshops(),
    ).annotate(
        presentations_total=F('presentations_generated') + F('presentations_regenerated'),
        reports_total=F('reports_generated') + F('reports_regenerated'),
    )


# ──────────────────────────────────────────────────────────────────────
# Views
# ──────────────────────────────────────────────────────────────────────
@staff_member_required
def admin_user_logs(request):
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
    from apps.notifications.models import notify

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

    if len(message_text) > 1000:
        message_text = message_text[:1000]

    kind = {
        'normal': 'admin_message',
        'important': 'admin_important',
        'urgent': 'admin_urgent',
    }[priority]

    recipients = User.objects.filter(id__in=user_ids, is_active=True)

    sent = 0
    failed = 0
    for user in recipients:
        try:
            notify(user, message_text, kind=kind, sent_by=request.user, priority=priority)
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