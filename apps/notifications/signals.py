"""
Signal receivers that create notifications and write audit log rows.

Every handler below is defensive:
  - Wrapped in try/except so a failure never breaks the user's action
  - Uses lazy app/model lookups to avoid circular imports
  - Respects `_skip_notification` flag if set
"""
import logging

from django.apps import apps
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .models import notify

logger = logging.getLogger(__name__)


def _get_client_ip(request):
    if request is None:
        return None
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _get_ua(request):
    if request is None:
        return ''
    return (request.META.get('HTTP_USER_AGENT') or '')[:300]


def _resolve_dailylog_user(instance):
    """
    DailyLog has no direct `user` FK — the user lives on `attachment.user`.
    Fall back gracefully so this works even if the schema ever changes.
    """
    user = getattr(instance, 'user', None)
    if user is not None:
        return user
    att = getattr(instance, 'attachment', None)
    return getattr(att, 'user', None)


# ──────────────────────────────────────────────────────────────────────
# DAILY LOG — created / hard-deleted
# ──────────────────────────────────────────────────────────────────────
@receiver(post_save, sender='logs.DailyLog')
def on_daily_log_saved(sender, instance, created, **kwargs):
    if not created:
        return
    if getattr(instance, '_skip_notification', False):
        return
    # Skip if the row was created already soft-deleted (defensive — unusual)
    if getattr(instance, 'is_deleted', False):
        return
    try:
        user = _resolve_dailylog_user(instance)
        if user is None:
            return
        date_str = instance.date.strftime('%b %d') if getattr(instance, 'date', None) else 'today'
        unit = getattr(instance, 'unit_sub_department', '') or 'a unit'
        notify(
            user,
            f'New daily log added at {unit} on {date_str}',
            kind='log_added',
        )
    except Exception:
        logger.exception('Failed to create log_added notification')


@receiver(post_delete, sender='logs.DailyLog')
def on_daily_log_deleted(sender, instance, **kwargs):
    """Only fires on hard-deletes. Soft-deletes are handled in views.py."""
    try:
        user = _resolve_dailylog_user(instance)
        if user is None:
            return
        unit = getattr(instance, 'unit_sub_department', '') or 'a unit'
        notify(
            user,
            f'Daily log removed from {unit}',
            kind='deletion',
        )
    except Exception:
        logger.exception('Failed to create deletion notification')


# ──────────────────────────────────────────────────────────────────────
# WORKSHOP — created
# ──────────────────────────────────────────────────────────────────────
@receiver(post_save, sender='logs.Workshop')
def on_workshop_saved(sender, instance, created, **kwargs):
    if not created:
        return
    if getattr(instance, '_skip_notification', False):
        return
    try:
        user = getattr(instance, 'user', None)
        if user is None:
            att = getattr(instance, 'attachment', None)
            user = getattr(att, 'user', None)
        if user is None:
            return
        activity = (
            getattr(instance, 'activity', None)
            or getattr(instance, 'title', None)
            or getattr(instance, 'name', None)
            or 'a workshop'
        )
        notify(user, f'Workshop added: {activity}', kind='workshop_added')
    except Exception:
        logger.exception('Failed to create workshop_added notification')


# ──────────────────────────────────────────────────────────────────────
# GENERATED REPORT
# ──────────────────────────────────────────────────────────────────────
# No signal handler here — apps.reports.views._notify_build() owns this.
# GeneratedReport rows are created on first visit to /reports/ (via
# get_or_create) before any file exists, so a post_save handler would
# fire false positives. The view fires the notification exactly once
# per successful build.


# ──────────────────────────────────────────────────────────────────────
# LOGIN / LOGOUT / FAILED LOGIN
# ──────────────────────────────────────────────────────────────────────
@receiver(user_logged_in)
def on_user_logged_in(sender, request, user, **kwargs):
    try:
        notify(user, 'You logged in', kind='login')
    except Exception:
        logger.exception('Failed to create login notification')

    try:
        from apps.accounts.models import UserActivityLog
        UserActivityLog.objects.create(
            user=user,
            action=UserActivityLog.ACTION_LOGIN,
            ip_address=_get_client_ip(request),
            user_agent=_get_ua(request),
        )
    except Exception:
        logger.exception('Failed to write login audit row')


@receiver(user_logged_out)
def on_user_logged_out(sender, request, user, **kwargs):
    if not user:
        return

    try:
        notify(user, 'You logged out', kind='logout')
    except Exception:
        logger.exception('Failed to create logout notification')

    try:
        from apps.accounts.models import UserActivityLog
        UserActivityLog.objects.create(
            user=user,
            action=UserActivityLog.ACTION_LOGOUT,
            ip_address=_get_client_ip(request),
            user_agent=_get_ua(request),
        )
    except Exception:
        logger.exception('Failed to write logout audit row')


@receiver(user_login_failed)
def on_user_login_failed(sender, credentials, request, **kwargs):
    try:
        from apps.accounts.models import UserActivityLog
        username = (credentials or {}).get('username', '') or ''
        UserActivityLog.objects.create(
            username_attempted=username[:150],
            action=UserActivityLog.ACTION_FAILED,
            ip_address=_get_client_ip(request),
            user_agent=_get_ua(request),
        )
    except Exception:
        logger.exception('Failed to write failed-login audit row')