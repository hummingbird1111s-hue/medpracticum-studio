from django.http import JsonResponse
from django.shortcuts import render
from django.templatetags.static import static


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


def service_worker(request):
    # Served from the site root so it can control every page.
    response = render(request, 'pwa/service-worker.js', content_type='application/javascript')
    response['Cache-Control'] = 'no-cache'
    return response


def offline(request):
    return render(request, 'pwa/offline.html')