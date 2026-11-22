from .models import user_is_subscribed


def subscription_status(request):
    """
    Adds to every template context:
      - user_has_subscription  (True iff active + non-expired)
      - show_payment_banner    (True iff logged in AND not subscribed)
    """
    if not request.user.is_authenticated:
        return {
            'user_has_subscription': False,
            'show_payment_banner': False,
        }
    subscribed = user_is_subscribed(request.user)
    return {
        'user_has_subscription': subscribed,
        'show_payment_banner': not subscribed,
    }