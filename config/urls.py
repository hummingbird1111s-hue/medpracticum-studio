from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView, TemplateView

from apps.payments.views import pwa_update


urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('apps.accounts.urls')),
    path('reports/', include('apps.reports.urls')),
    path('payments/', include('apps.payments.urls')),   # ✅ subscriptions + Paystack
    path('', include('apps.logs.urls')),
    path('update/', pwa_update, name='pwa_update'),


    # ✅ PWA: offline fallback — matches `OFFLINE_URL = '/offline/'` in service-worker.js.
    # Used by the SW when both cache and network fail during a navigation.
    path('offline/', TemplateView.as_view(
        template_name='offline.html',
        content_type='text/html; charset=utf-8',
    ), name='offline'),

    # Silence the browser's automatic /favicon.ico request by pointing it at the PWA icon.
    path('favicon.ico',
         RedirectView.as_view(url=settings.STATIC_URL + 'icons/icon-192.png', permanent=True)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)