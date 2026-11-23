from django.http import JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods


def manifest(request):
    data = {
        'name': 'MedPracticum Studio',
        'short_name': 'MedPracticum',
        'description': 'Daily practicum logs for Yamfo College of Health students.',
        'start_url': '/',
        'scope': '/',
        'display': 'standalone',
        'background_color': '#ffffff',
        'theme_color': '#059669',
        'icons': [
            {'src': static('icons/icon-192.png'), 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any'},
            {'src': static('icons/icon-512.png'), 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any'},
            {'src': static('icons/icon-maskable-512.png'), 'sizes': '512x512', 'type': 'image/png',
             'purpose': 'maskable'},
        ],
    }
    return JsonResponse(data, content_type='application/manifest+json')


@require_http_methods(["GET", "HEAD"])
@never_cache
def service_worker(request):
    """
    Serve /service-worker.js from templates/pwa/service-worker.js.

    Critical headers:
      - Cache-Control: no-cache, no-store, must-revalidate
            → forces the browser to re-fetch on every reg.update() call,
              so a VERSION bump in the template is detected immediately.
              Without this, the browser heuristically caches the SW and the
              update banner would never fire.
      - Service-Worker-Allowed: /
            → explicitly permits scope: '/' even though the file lives at
              templates/pwa/service-worker.js (nested).
    """
    response = render(
        request,
        'pwa/service-worker.js',
        content_type='application/javascript',
    )
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    response['Service-Worker-Allowed'] = '/'
    return response


def offline(request):
    return render(request, 'pwa/offline.html')