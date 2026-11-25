from .models import Subscription, get_subscription


def subscription_status(request):
    """
    Adds to every template context:
      - user_has_subscription           (bool — active AND non-expired)
      - show_payment_banner             (bool — nag user to pay)
      - user_subscription               (Subscription object or None)
      - user_subscription_status        ('subscribed'|'partial'|'pending'|'unsubscribed')
      - user_subscription_status_label  (human label for the pill)
      - user_subscription_status_class  (CSS modifier for the pill)

    The status key is consumed by base.html's sidebar badge:
      'subscribed'   → ⭐ Subscription Active   (green)
      'partial'      → 🌱 Next Period Covered   (blue)
      'pending'      → ⏳ Pending Verification  (purple)
      anything else  → ✨ Unlock Full Potential (amber)

    State priority (top wins):
      1. status == 'pending'                     → 'pending'
      2. status == 'partial'                     → 'partial'
      3. status == 'subscribed' AND not expired  → 'subscribed'
      4. status == 'subscribed' AND expired:
           a) plan == 'double'  → 'partial'   ← AUTO-DETECT between periods
           b) plan == 'single'  → 'unsubscribed'  (need to renew)
      5. no subscription, or status == 'unsubscribed' → 'unsubscribed'

    Case 4a — the fix in this version:
      A user who paid for BOTH periods is activated with
      expires_at = period-1-end + 5 days. When that expires_at passes,
      we do NOT want to flip them to 'unsubscribed' and nag them to pay
      AGAIN — they already paid for period 2. So if plan is 'double'
      and the subscription is expired, we show 'partial'
      ("Next Period Covered") until the admin either reactivates them
      for period 2 (mark_subscribed_reactivate) or, if they've already
      finished both periods, manually marks them 'unsubscribed'
      (mark_unsubscribed). Both admin actions already exist.
    """

    anonymous = {
        'user_has_subscription': False,
        'show_payment_banner': False,
        'user_subscription': None,
        'user_subscription_status': 'unsubscribed',
        'user_subscription_status_label': 'Unlock Full Potential',
        'user_subscription_status_class': 'inactive',
    }

    if not request.user.is_authenticated:
        return anonymous

    try:
        sub = get_subscription(request.user)
    except Exception:
        # Never let a subscriptions lookup take down every page in the app.
        return anonymous

    raw_status = getattr(sub, 'status', None) if sub else None
    is_active = bool(sub and getattr(sub, 'is_currently_active', False))

    # Constants — read via getattr so a future migration rename can't 500 every page.
    STATUS_PENDING    = getattr(Subscription, 'STATUS_PENDING',    'pending')
    STATUS_PARTIAL    = getattr(Subscription, 'STATUS_PARTIAL',    'partial')
    STATUS_SUBSCRIBED = getattr(Subscription, 'STATUS_SUBSCRIBED', 'subscribed')
    PLAN_DOUBLE       = getattr(Subscription, 'PLAN_DOUBLE',       'double')

    # ── 1. Pending / partial pre-states short-circuit first: the user
    #       needs to see them regardless of whether the CURRENT period
    #       is technically still active.
    if raw_status == STATUS_PENDING:
        status_key = 'pending'
        label = 'Pending Verification'
        css_class = 'pending'
        show_banner = False

    elif raw_status == STATUS_PARTIAL:
        status_key = 'partial'
        label = 'Next Period Covered'
        css_class = 'partial'
        show_banner = False

    # ── 2. Active subscription (status='subscribed' AND not expired).
    elif is_active:
        status_key = 'subscribed'
        label = 'Subscription Active'
        css_class = 'active'
        show_banner = False

    # ── 3. Expired DOUBLE-plan subscription → 'partial'  ← THE FIX.
    #       They paid for both periods; the first ended but period 2 is
    #       already covered. Show "Next Period Covered" instead of nagging.
    elif (sub
          and raw_status == STATUS_SUBSCRIBED
          and getattr(sub, 'plan', '') == PLAN_DOUBLE):
        status_key = 'partial'
        label = 'Next Period Covered'
        css_class = 'partial'
        show_banner = False

    # ── 4. Everything else → 'unsubscribed'.
    #       Covers:
    #         • sub is None
    #         • status == 'unsubscribed'
    #         • status == 'subscribed' but expired on a SINGLE plan
    #           (they need to renew to regain access)
    else:
        status_key = 'unsubscribed'
        label = 'Unlock Full Potential'
        css_class = 'inactive'
        show_banner = True

    return {
        'user_has_subscription': is_active,
        'show_payment_banner': show_banner,
        'user_subscription': sub,
        'user_subscription_status': status_key,
        'user_subscription_status_label': label,
        'user_subscription_status_class': css_class,
    }