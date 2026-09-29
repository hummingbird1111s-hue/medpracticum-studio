import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from .department_config import COMMON_TEXT_FIELDS, get_department_config
from .forms import AttachmentSessionForm
from .models import DailyLog

MAX_LOGS_PER_SYNC = 100
MAX_TAGS = 20


# ---------- Pages ----------

@login_required
def dashboard(request):
    session = request.user.attachments.order_by('-start_date', '-id').first()
    recent_logs = []
    total_logs = 0
    if session:
        recent_logs = session.daily_logs.order_by('-date')[:5]
        total_logs = session.daily_logs.count()

    return render(request, 'logs/dashboard.html', {
        'session': session,
        'recent_logs': recent_logs,
        'total_logs': total_logs,
    })


@login_required
def start_attachment(request):
    if request.method == 'POST':
        form = AttachmentSessionForm(request.POST)
        if form.is_valid():
            session = form.save(commit=False)
            session.user = request.user
            session.save()
            return redirect('dashboard')
    else:
        form = AttachmentSessionForm()

    return render(request, 'logs/start_attachment.html', {'form': form})


def _safe_parse_date(value):
    try:
        return parse_date(value or '')
    except ValueError:
        return None


@login_required
def new_log(request):
    session = request.user.attachments.order_by('-start_date').first()
    if session is None:
        return redirect('start_attachment')

    dept = get_department_config(request.user.department)

    existing = None
    requested_date = _safe_parse_date(request.GET.get('date'))
    if requested_date:
        log = session.daily_logs.filter(date=requested_date).first()
        if log:
            existing = {
                'date': log.date.isoformat(),
                'unit_sub_department': log.unit_sub_department,
                'activity_summary': log.activity_summary,
                'skills_acquired': log.skills_acquired,
                'challenges_faced': log.challenges_faced,
                'solutions_applied': log.solutions_applied,
                'tags': log.tags,
                'tallies': log.numeric_tallies,
                'details': log.department_details,
            }

    config = {
        'attachment_id': session.id,
        'start_date': session.start_date.isoformat(),
        'end_date': session.end_date.isoformat(),
        'today': timezone.localdate().isoformat(),
        'tallies': dept['tallies'],
        'details': dept['details'],
        'suggested_tags': dept['tags'],
        'dashboard_url': reverse('dashboard'),
        'existing': existing,
    }

    return render(request, 'logs/new_log.html', {
        'session': session,
        'dept': dept,
        'text_fields': COMMON_TEXT_FIELDS,
        'config': config,
    })


# ---------- Sync API ----------

def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_tags(raw):
    if not isinstance(raw, list):
        return []
    cleaned = []
    for tag in raw:
        text = str(tag).strip()[:40]
        if not text:
            continue
        if not text.startswith('#'):
            text = '#' + text
        if text not in cleaned:
            cleaned.append(text)
    return cleaned[:MAX_TAGS]


def _clean_tallies(raw):
    if not isinstance(raw, dict):
        return {}
    cleaned = {}
    for key, value in raw.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number < 0:
            continue
        cleaned[str(key)[:50]] = int(number) if number.is_integer() else number
    return cleaned


def _clean_details(raw):
    if not isinstance(raw, dict):
        return {}
    cleaned = {}
    for key, value in raw.items():
        text = str(value).strip()[:2000]
        if text:
            cleaned[str(key)[:50]] = text
    return cleaned


def _save_item(sessions, item):
    session = sessions.get(_to_int(item.get('attachment_id')))
    if session is None:
        raise ValueError('Unknown attachment session.')

    try:
        log_date = parse_date(str(item.get('date', '')))
    except ValueError:
        log_date = None
    if log_date is None:
        raise ValueError('Invalid date.')
    if not (session.start_date <= log_date <= session.end_date):
        raise ValueError('That date is outside your attachment period.')

    unit = str(item.get('unit_sub_department', '')).strip()[:100]
    summary = str(item.get('activity_summary', '')).strip()
    if not unit:
        raise ValueError('Unit is required.')
    if not summary:
        raise ValueError('Activity summary is required.')

    DailyLog.objects.update_or_create(
        attachment=session,
        date=log_date,
        defaults={
            'week_number': (log_date - session.start_date).days // 7 + 1,
            'unit_sub_department': unit,
            'activity_summary': summary,
            'tags': _clean_tags(item.get('tags')),
            'numeric_tallies': _clean_tallies(item.get('numeric_tallies')),
            'department_details': _clean_details(item.get('department_details')),
            'skills_acquired': str(item.get('skills_acquired', '')).strip(),
            'challenges_faced': str(item.get('challenges_faced', '')).strip(),
            'solutions_applied': str(item.get('solutions_applied', '')).strip(),
            'created_offline': bool(item.get('created_offline')),
        },
    )


@require_POST
def sync_logs(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'authentication required'}, status=401)

    try:
        items = json.loads(request.body)['logs']
        if not isinstance(items, list):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        return JsonResponse({'error': 'invalid payload'}, status=400)

    sessions = {s.id: s for s in request.user.attachments.all()}
    synced, errors = [], []

    for item in items[:MAX_LOGS_PER_SYNC]:
        if not isinstance(item, dict):
            errors.append({'key': '', 'error': 'Invalid log entry.'})
            continue
        key = str(item.get('key', ''))
        try:
            _save_item(sessions, item)
            synced.append(key)
        except ValueError as exc:
            errors.append({'key': key, 'error': str(exc)})

    return JsonResponse({'synced': synced, 'errors': errors})