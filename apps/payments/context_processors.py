from .models import Subscription, get_subscription


def subscription_status(request):
    """
    Adds to every template context:
      - user_has_subscription        (bool — active + non-expired)
      - show_payment_banner          (bool — nag user to pay)
      - user_subscription            (Subscription object or None)
      - user_subscription_status     ('unsubscribed'|'pending'|'partial'|'subscribed')
      - user_subscription_status_label  (human label for the pill)
      - user_subscription_status_class  (CSS modifier for the pill)
    """
    if not request.user.is_authenticated:
        return {
            'user_has_subscription': False,
            'show_payment_banner': False,
            'user_subscription': None,
            'user_subscription_status': '',
            'user_subscription_status_label': '',
            'user_subscription_status_class': '',
        }

    sub = get_subscription(request.user)
    subscribed = bool(sub and sub.is_currently_active)

    if sub is None or sub.status == Subscription.STATUS_UNSUBSCRIBED:
        status_key, label, css_class = 'unsubscribed', 'Not subscribed', 'not-sub'
    elif sub.status == Subscription.STATUS_PENDING:
        status_key, label, css_class = 'pending', 'Pending', 'pending'
    elif sub.status == Subscription.STATUS_PARTIAL:
        status_key, label, css_class = 'partial', 'Partial', 'partial'
    else:
        status_key, label, css_class = 'subscribed', 'Subscribed', 'subscribed'

    # Don't nag people who already paid (subscribed/pending/partial)
    nag = not subscribed and status_key not in ('pending', 'partial')

    return {
        'user_has_subscription': subscribed,
        'show_payment_banner': nag,
        'user_subscription': sub,
        'user_subscription_status': status_key,
        'user_subscription_status_label': label,
        'user_subscription_status_class': css_class,
    }