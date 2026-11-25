from datetime import datetime, timedelta

from django.contrib import admin
from django.utils import timezone

from .models import Subscription, _compute_expiry_for_session


def _notify_user_activated(user, plan, expires_at):
    """
    Fire a bell notification when an admin activates a subscription.

    Wrapped in try/except so a notification failure never blocks the
    admin action itself — the subscription is the source of truth.
    """
    try:
        from apps.notifications.models import notify
    except Exception:
        return

    plan_label = 'Both Periods' if plan == Subscription.PLAN_DOUBLE else 'Single Period'
    expiry_label = expires_at.strftime('%d %b %Y') if expires_at else 'N/A'
    try:
        notify(
            user,
            f'✅ Subscription activated ({plan_label}) — valid until {expiry_label}',
            kind='payment_submitted',
        )
    except Exception:
        pass


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'status', 'plan', 'activated_at', 'expires_at',
        'days_remaining', 'last_amount_paid', 'last_reference',
    )
    list_filter = ('status', 'plan')
    search_fields = (
        'user__username', 'user__email',
        'last_reference', 'pending_reference',
    )
    readonly_fields = (
        'created_at', 'updated_at', 'days_remaining', 'pending_submitted_at',
    )
    date_hierarchy = 'created_at'

    actions = [
        'approve_single_50',
        'approve_double_95',
        'mark_partial',
        'mark_subscribed_reactivate',
        'mark_unsubscribed',
    ]

    fieldsets = (
        (None, {
            'fields': ('user', 'status', 'plan'),
        }),
        ('Active period', {
            'fields': (
                'activated_at', 'expires_at',
                'period_start', 'period_end', 'days_remaining',
            ),
        }),
        ('Pending verification', {
            'fields': (
                'pending_plan', 'pending_reference', 'pending_submitted_at',
            ),
            'classes': ('collapse',),
        }),
        ('Last confirmed payment', {
            'fields': ('last_reference', 'last_amount_paid'),
            'classes': ('collapse',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    @admin.display(description='Days left')
    def days_remaining(self, obj):
        return obj.days_remaining

    # ------------------------------------------------------------------
    # Shared helper — approve with a plan and compute expiry from attachment
    # ------------------------------------------------------------------
    def _approve(self, request, queryset, plan, amount):
        count = 0
        skipped = 0
        for sub in queryset:
            att = (
                sub.user.attachments
                .filter(is_deleted=False)
                .order_by('-start_date', '-id')
                .first()
            )
            if not att:
                skipped += 1
                continue

            sub.status = Subscription.STATUS_SUBSCRIBED
            sub.plan = plan
            sub.activated_at = timezone.now()
            sub.expires_at = _compute_expiry_for_session(att)
            sub.period_start = att.start_date
            sub.period_end = att.end_date
            sub.last_amount_paid = amount
            if sub.pending_reference:
                sub.last_reference = sub.pending_reference
            sub.pending_plan = ''
            sub.pending_reference = ''
            sub.pending_submitted_at = None
            sub.save()

            # Bell-notify the user their access is live
            _notify_user_activated(sub.user, plan, sub.expires_at)

            count += 1

        msg = f'{count} subscription(s) approved as {plan} (₵{amount}).'
        if skipped:
            msg += f' {skipped} skipped — no attachment on file.'
        self.message_user(request, msg)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    @admin.action(description='✅ Approve — Single Period plan (₵50)')
    def approve_single_50(self, request, queryset):
        self._approve(
            request, queryset,
            Subscription.PLAN_SINGLE, Subscription.PRICE_SINGLE,
        )

    @admin.action(description='✅ Approve — Both Periods plan (₵95)')
    def approve_double_95(self, request, queryset):
        self._approve(
            request, queryset,
            Subscription.PLAN_DOUBLE, Subscription.PRICE_DOUBLE,
        )

    @admin.action(description='🌱 Mark as Partial (95-plan, between periods)')
    def mark_partial(self, request, queryset):
        updated = queryset.update(status=Subscription.STATUS_PARTIAL)
        self.message_user(
            request,
            f'{updated} subscription(s) marked as partial. '
            f'They stay covered — reactivate when they start their next attachment.',
        )

    @admin.action(description='🔓 Reactivate Partial as Subscribed (using current attachment)')
    def mark_subscribed_reactivate(self, request, queryset):
        count = 0
        skipped = 0
        for sub in queryset:
            att = (
                sub.user.attachments
                .filter(is_deleted=False)
                .order_by('-start_date', '-id')
                .first()
            )
            if not att:
                skipped += 1
                continue
            sub.status = Subscription.STATUS_SUBSCRIBED
            sub.activated_at = timezone.now()
            sub.expires_at = _compute_expiry_for_session(att)
            sub.period_start = att.start_date
            sub.period_end = att.end_date
            sub.save()

            # Reactivation is also a user-facing win — fire the bell
            _notify_user_activated(
                sub.user,
                sub.plan or Subscription.PLAN_DOUBLE,
                sub.expires_at,
            )

            count += 1

        msg = f'{count} reactivated with new attachment dates.'
        if skipped:
            msg += f' {skipped} skipped — no attachment on file.'
        self.message_user(request, msg)

    @admin.action(description='🔒 Mark as Unsubscribed')
    def mark_unsubscribed(self, request, queryset):
        updated = queryset.update(status=Subscription.STATUS_UNSUBSCRIBED)
        self.message_user(
            request,
            f'{updated} subscription(s) marked as unsubscribed. '
            f'They will need to pay again to regain access.',
        )