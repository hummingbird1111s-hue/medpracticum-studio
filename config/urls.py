from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('apps.accounts.urls')),
    path('reports/', include('apps.reports.urls')),
    path('payments/', include('apps.payments.urls')),   # ✅ NEW: subscriptions + Paystack
    path('', include('apps.logs.urls')),

    # Silence the browser's automatic /favicon.ico request by pointing it at the PWA icon.
    path('favicon.ico',
         RedirectView.as_view(url=settings.STATIC_URL + 'icons/icon-192.png', permanent=True)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)