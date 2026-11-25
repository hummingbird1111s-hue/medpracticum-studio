from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView, TemplateView

from apps.accounts import admin_views
from apps.payments import admin_views as payments_admin_views
from apps.payments.views import pwa_update


urlpatterns = [
    path('admin/', admin.site.urls),

    # Public landing page — used for marketing + Paystack merchant verification.
    # Anonymous-safe: no login required. Visit /welcome/ directly.
    path(
        'welcome/',
        TemplateView.as_view(template_name='landing.html'),
        name='landing',
    ),

    path('accounts/', include('apps.accounts.urls')),
    path('reports/', include('apps.reports.urls')),
    path('payments/', include('apps.payments.urls')),
    path('', include('apps.logs.urls')),
    path('update/', pwa_update, name='pwa_update'),

    # Notifications API (badge + bell dropdown)
    path('api/notifications/', include('apps.notifications.urls')),

    # Admin-only pages
    path('admin-workspace/user-logs/', admin_views.admin_user_logs, name='admin_user_logs'),
    path('admin-workspace/all-users/', admin_views.admin_all_users, name='admin_all_users'),
    path('admin-workspace/send-message/', admin_views.admin_send_message, name='admin_send_message'),
    path('admin-workspace/payments/', payments_admin_views.admin_pending_payments, name='admin_pending_payments'),
    path('admin-workspace/payments/verify/<int:pk>/', payments_admin_views.admin_verify_payment, name='admin_verify_payment'),

    # PWA: offline fallback
    path('offline/', TemplateView.as_view(
        template_name='offline.html',
        content_type='text/html; charset=utf-8',
    ), name='offline'),

    path('favicon.ico',
         RedirectView.as_view(url=settings.STATIC_URL + 'icons/icon-192.png', permanent=True)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)