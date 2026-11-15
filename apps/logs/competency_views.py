# apps/logs/competency_views.py
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods, require_POST

from .models import AttachmentSession, AttachmentCompetency
from .competency_ocr import transcribe_competency_image, parse_competency_lines


@login_required
@require_http_methods(["GET", "POST"])
def attachment_competencies(request, session_id):
    """GET: list competencies. POST: replace the full list."""
    session = get_object_or_404(AttachmentSession, pk=session_id, user=request.user)

    if request.method == 'GET':
        return JsonResponse({
            'competencies': [
                {'id': c.id, 'text': c.text, 'source': c.source}
                for c in session.competencies.all()
            ],
            'attachment': {
                'id': session.id,
                'facility': session.facility.name if session.facility_id else '',
            },
        })

    # ── POST: replace the full list ────────────────────────────
    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    items = payload.get('competencies')
    if not isinstance(items, list):
        return JsonResponse({'error': '"competencies" must be a list'}, status=400)

    cleaned = []
    for raw in items:
        if isinstance(raw, str):
            text, source = raw, AttachmentCompetency.SOURCE_MANUAL
        elif isinstance(raw, dict):
            text   = (raw.get('text') or '').strip()
            source = raw.get('source') or AttachmentCompetency.SOURCE_MANUAL
        else:
            continue
        text = text.strip()
        if not text:
            continue
        if len(text) > 500:
            text = text[:500]
        if source not in dict(AttachmentCompetency.SOURCE_CHOICES):
            source = AttachmentCompetency.SOURCE_MANUAL
        cleaned.append((text, source))

    session.competencies.all().delete()
    AttachmentCompetency.objects.bulk_create([
        AttachmentCompetency(
            attachment_session=session, text=t, source=s, order=i,
        ) for i, (t, s) in enumerate(cleaned)
    ])

    return JsonResponse({'success': True, 'count': len(cleaned)})


@login_required
@require_POST
def upload_competency_image(request):
    """Accepts an image or PDF, returns transcribed competency text."""
    image = request.FILES.get('image')
    if not image:
        return JsonResponse({'error': 'No file uploaded'}, status=400)
    
    if image.size > 10 * 1024 * 1024:
        return JsonResponse({'error': 'File too large (max 10 MB)'}, status=400)
        
    # Allow images, PDFs, and plain text files
    valid_types = ('image/', 'application/pdf', 'text/plain')
    if not any((image.content_type or '').startswith(t) for t in valid_types):
        return JsonResponse({'error': 'Only images, PDFs, or text files are supported'}, status=400)

    image_bytes = image.read()
    mime = image.content_type or 'image/jpeg'

    try:
        raw = transcribe_competency_image(image_bytes, mime=mime)
    except RuntimeError as exc:
        return JsonResponse({'error': str(exc)}, status=503)
    except Exception as exc:
        return JsonResponse({'error': f'Transcription failed: {exc}'}, status=500)

    lines = parse_competency_lines(raw)
    if not lines:
        return JsonResponse(
            {'error': 'No readable text found. Try a clearer file.'},
            status=422,
        )

    return JsonResponse({
        'text': '\n'.join(lines),
        'lines': lines,
        'count': len(lines),
    })