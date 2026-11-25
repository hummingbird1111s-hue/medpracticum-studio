from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.shortcuts import render
from django.urls import include, path
from django.views.generic import RedirectView, TemplateView

from apps.accounts import admin_views
from apps.logs import views as logs_views
from apps.payments import admin_views as payments_admin_views
from apps.payments.views import pwa_update


def root_view(request):
    """
    Public entry point at '/'.

    ALWAYS serves the landing page, for everyone, on every visit — even
    if the user is already logged in. This is what makes the site link
    a clean "entry point" every time it's clicked.

    PWA users escape this via a small JS redirect in landing.html
    (display-mode: standalone → /dashboard/). Browser users see the
    landing page and can click "Continue to Dashboard" if logged in.
    """
    return render(request, 'landing.html')


urlpatterns = [
    path('admin/', admin.site.urls),

    # Root: ALWAYS landing.html. The dashboard lives at /dashboard/ (below).
    # Do NOT change this to branch on auth — landing.html itself shows
    # "Continue to Dashboard" for logged-in users.
    path('', root_view, name='root'),

    # Alias for Paystack submission + external links
    path(
        'welcome/',
        TemplateView.as_view(template_name='landing.html'),
        name='landing',
    ),

    path('accounts/', include('apps.accounts.urls')),
    path('reports/', include('apps.reports.urls')),
    path('payments/', include('apps.payments.urls')),
    path('', include('apps.logs.urls')),  # dashboard + other logs routes
    path('update/', pwa_update, name='pwa_update'),

    path('api/notifications/', include('apps.notifications.urls')),

    path('admin-workspace/user-logs/', admin_views.admin_user_logs, name='admin_user_logs'),
    path('admin-workspace/all-users/', admin_views.admin_all_users, name='admin_all_users'),
    path('admin-workspace/send-message/', admin_views.admin_send_message, name='admin_send_message'),
    path('admin-workspace/payments/', payments_admin_views.admin_pending_payments, name='admin_pending_payments'),
    path('admin-workspace/payments/verify/<int:pk>/', payments_admin_views.admin_verify_payment, name='admin_verify_payment'),

    # ------------------------------------------------------------------
    # DASHBOARD OVERRIDE — MUST stay AFTER the logs.urls include.
    #
    # logs.urls mounts its dashboard view at '' with name='dashboard',
    # which makes reverse('dashboard') → '/' (the landing page) → LOOP
    # when the user clicks "Continue to Dashboard" or the PWA-escape
    # script fires.
    #
    # Placing this AFTER the include makes:
    #   • reverse('dashboard')  → '/dashboard/'   (last-name-wins)
    #   • GET /dashboard/       → this view       (first-URL-match for
    #                                              /dashboard/, since the
    #                                              '' pattern in logs.urls
    #                                              only matches the empty
    #                                              path, not '/dashboard/')
    # ------------------------------------------------------------------
    path('dashboard/', logs_views.dashboard, name='dashboard'),

    path('offline/', TemplateView.as_view(
        template_name='offline.html',
        content_type='text/html; charset=utf-8',
    ), name='offline'),

    path('favicon.ico',
         RedirectView.as_view(url=settings.STATIC_URL + 'icons/icon-192.png', permanent=True)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)