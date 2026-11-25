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
      'subscribed'   → ⭐ Subscription Active  (green)
      'partial'      → 🌱 Next Period Covered  (blue)
      'pending'      → ⏳ Pending Verification (purple)
      anything else  → ✨ Unlock Full Potential (amber)

    Fixes vs the previous version:
      1. A row with status='subscribed' but is_currently_active=False
         (i.e. EXPIRED) now correctly falls into the 'unsubscribed'
         branch → amber pill + nag banner. Before, it stayed 'subscribed'
         so the user never learned they needed to renew.
      2. user_subscription_status_class uses 'active' (not 'not-sub')
         to match base.html's mp-sidebar__subscription--active suffix.
      3. Wraps get_subscription() in try/except so a single bad row can
         never 500 every page in the app.
      4. 'pending' and 'partial' short-circuit BEFORE the is_active check,
         so a pending renewal still shows the right pill even if the
         current period is technically still active.
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

    # ── Pre-payment states take precedence: the user needs to see
    #    them regardless of whether the CURRENT period is active.
    if raw_status == getattr(Subscription, 'STATUS_PENDING', 'pending'):
        status_key = 'pending'
        label = 'Pending Verification'
        css_class = 'pending'
        show_banner = False

    elif raw_status == getattr(Subscription, 'STATUS_PARTIAL', 'partial'):
        status_key = 'partial'
        label = 'Next Period Covered'
        css_class = 'partial'
        show_banner = False

    elif is_active:
        status_key = 'subscribed'
        label = 'Subscription Active'
        css_class = 'active'
        show_banner = False

    else:
        # Covers three cases:
        #   • sub is None
        #   • sub.status == UNSUBSCRIBED
        #   • sub.status == SUBSCRIBED but is_currently_active == False (EXPIRED)
        # All three → no active subscription right now → amber + banner.
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