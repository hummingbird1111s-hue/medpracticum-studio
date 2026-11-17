import json
import base64
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

# ─────────────────────────────────────────────────────────────────────────
# CRITICAL FOR RENDER FREE TIER:
# Do NOT import `google.genai` at module level. The SDK builds an
# enormous Pydantic model tree on import (~200 MB) and causes Gunicorn
# workers to be SIGKILL'd for OOM during startup. Instead, we check
# `settings.GEMINI_API_KEY` (cheap) and let `.ai_client` load the SDK
# lazily inside `ai_chat()` only when an actual AI request comes in.
# ─────────────────────────────────────────────────────────────────────────

from .department_config import COMMON_TEXT_FIELDS, get_department_config
from .forms import AttachmentSessionForm
from .models import (
    AttachmentSession, DailyLog, Workshop,
    AIChatSession, AIChatMessage, AttachmentCompetency,
)

MAX_LOGS_PER_SYNC = 100
MAX_TAGS = 20


def _ai_is_configured():
    """Cheap check — does not touch the heavy google.genai SDK."""
    return bool(getattr(settings, 'GEMINI_API_KEY', None))


def _get_active_session(request):
    req_id = request.GET.get('switch_attachment') or request.GET.get('attachment_id')
    if req_id:
        att = request.user.attachments.filter(id=req_id, is_deleted=False).first()
        if att:
            request.session['active_attachment_id'] = att.id
            return att
    active_id = request.session.get('active_attachment_id')
    if active_id:
        att = request.user.attachments.filter(id=active_id, is_deleted=False).first()
        if att:
            return att
    att = request.user.attachments.filter(is_deleted=False).order_by('-start_date', '-id').first()
    if att:
        request.session['active_attachment_id'] = att.id
    return att


# ---------- Pages ----------

@login_required
def dashboard(request):
    session = _get_active_session(request)
    all_attachments = request.user.attachments.filter(is_deleted=False).order_by('-start_date', '-id')

    recent_logs = []
    total_logs = 0
    session_competencies = []

    if session:
        recent_logs = session.daily_logs.filter(is_deleted=False).order_by('-date')[:3]
        total_logs = session.daily_logs.filter(is_deleted=False).count()
        session_competencies = session.competencies.all()

    active_attachments_count = all_attachments.count()

    active_logs_count = DailyLog.objects.filter(
        attachment__user=request.user,
        attachment__is_deleted=False,
        is_deleted=False
    ).count()

    return render(request, 'logs/dashboard.html', {
        'session': session,
        'all_attachments': all_attachments,
        'recent_logs': recent_logs,
        'total_logs': total_logs,
        'active_attachments_count': active_attachments_count,
        'active_logs_count': active_logs_count,
        'session_competencies': session_competencies,
    })


def how_to_use(request):
    """Renders the detailed user guide. Public — reachable while logged out."""
    return render(request, 'logs/how_to_use.html')


def terms_and_conditions(request):
    """Renders the platform Terms, Conditions & Privacy Policy page. Public."""
    return render(request, 'logs/terms.html')


@login_required
def start_attachment(request):
    if request.method == 'POST':
        form = AttachmentSessionForm(request.POST)
        if form.is_valid():
            session = form.save(commit=False)
            session.user = request.user
            session.save()

            # ── Save competencies (optional, best-effort) ────────
            raw = request.POST.get('competencies_json') or '[]'
            try:
                items = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                items = []

            cleaned = []
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, str):
                        text, source = item, AttachmentCompetency.SOURCE_MANUAL
                    elif isinstance(item, dict):
                        text   = (item.get('text') or '').strip()
                        source = item.get('source') or AttachmentCompetency.SOURCE_MANUAL
                    else:
                        continue
                    if not text:
                        continue
                    if len(text) > 500:
                        text = text[:500]
                    if source not in dict(AttachmentCompetency.SOURCE_CHOICES):
                        source = AttachmentCompetency.SOURCE_MANUAL
                    cleaned.append((text, source))

            if cleaned:
                AttachmentCompetency.objects.bulk_create([
                    AttachmentCompetency(
                        attachment_session=session,
                        text=t, source=s, order=i,
                    ) for i, (t, s) in enumerate(cleaned)
                ])

            request.session['active_attachment_id'] = session.id
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
    session = _get_active_session(request)
    if session is None:
        return redirect('start_attachment')

    dept = get_department_config(request.user.department)
    existing = None
    edit_id = request.GET.get('edit_id')
    log = None
    if edit_id:
        log = session.daily_logs.filter(id=edit_id, is_deleted=False).first()
    if log:
        existing = {
            'id': log.id,
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

    all_attachments = request.user.attachments.filter(is_deleted=False).order_by('-start_date', '-id')

    return render(request, 'logs/new_log.html', {
        'session': session,
        'all_attachments': all_attachments,
        'dept': dept,
        'text_fields': COMMON_TEXT_FIELDS,
        'config': config,
    })


@login_required
def attachments_list(request):
    active_logs = DailyLog.objects.filter(is_deleted=False)
    attachments = request.user.attachments.filter(is_deleted=False).prefetch_related(
        Prefetch('daily_logs', queryset=active_logs, to_attr='active_logs')
    ).order_by('-start_date', '-id')

    return render(request, 'logs/attachments_list.html', {
        'attachments': attachments,
        'active_id': request.session.get('active_attachment_id')
    })


@login_required
def logs_list(request):
    active_logs = DailyLog.objects.filter(is_deleted=False)
    attachments = request.user.attachments.filter(is_deleted=False).prefetch_related(
        Prefetch('daily_logs', queryset=active_logs, to_attr='active_logs')
    ).order_by('-start_date', '-id')
    return render(request, 'logs/logs_list.html', {'attachments': attachments})


@login_required
def archive_list(request):
    view_type = request.GET.get('type', 'attachments')
    deleted_attachments = AttachmentSession.objects.filter(user=request.user, is_deleted=True).order_by('-start_date')
    deleted_logs = DailyLog.objects.filter(attachment__user=request.user, is_deleted=True).order_by('-date')
    return render(request, 'logs/archive.html', {
        'view_type': view_type,
        'deleted_attachments': deleted_attachments,
        'deleted_logs': deleted_logs,
    })


@login_required
def workshops_list(request):
    session = _get_active_session(request)
    if not session:
        return redirect('start_attachment')
    edit_workshop = None
    edit_id = request.GET.get('edit')
    if edit_id:
        edit_workshop = Workshop.objects.filter(pk=edit_id, attachment=session).first()
    if request.method == 'POST':
        date = request.POST.get('date')
        activity = request.POST.get('activity')
        w_type = request.POST.get('type', 'Workshop')
        if date and activity:
            if edit_workshop:
                edit_workshop.date = date
                edit_workshop.activity = activity
                edit_workshop.type = w_type
                edit_workshop.save()
                messages.success(request, 'Workshop updated successfully.')
            else:
                Workshop.objects.create(attachment=session, date=date, activity=activity, type=w_type)
                messages.success(request, 'Workshop added successfully.')
            return redirect(f"{reverse('workshops_list')}?attachment_id={session.id}")
    workshops = session.workshops.all().order_by('date')
    all_attachments = request.user.attachments.filter(is_deleted=False).order_by('-start_date', '-id')
    return render(request, 'logs/workshops.html', {
        'session': session,
        'workshops': workshops,
        'all_attachments': all_attachments,
        'edit_workshop': edit_workshop,
    })


@login_required
@require_POST
def delete_workshop(request, pk):
    workshop = Workshop.objects.filter(pk=pk, attachment__user=request.user).first()
    if workshop:
        attachment_id = workshop.attachment.id
        workshop.delete()
        messages.success(request, 'Workshop deleted.')
        return redirect(f"{reverse('workshops_list')}?attachment_id={attachment_id}")
    return redirect('workshops_list')


@login_required
@require_POST
def delete_attachment(request, pk):
    attachment = AttachmentSession.objects.filter(pk=pk, user=request.user).first()
    if attachment:
        if request.session.get('active_attachment_id') == attachment.id:
            del request.session['active_attachment_id']
        attachment.is_deleted = True
        attachment.save()
        attachment.daily_logs.update(is_deleted=True)
        messages.success(request, 'Attachment moved to archive.')
    else:
        messages.error(request, 'Attachment not found.')
    return redirect('attachments_list')


@login_required
@require_POST
def restore_attachment(request, pk):
    attachment = AttachmentSession.objects.filter(pk=pk, user=request.user, is_deleted=True).first()
    if attachment:
        attachment.is_deleted = False
        attachment.save()
        attachment.daily_logs.update(is_deleted=False)
        messages.success(request, 'Attachment restored successfully.')
    else:
        messages.error(request, 'Archived attachment not found.')
    return redirect('archive_list')


@login_required
@require_POST
def delete_log(request, pk):
    log = DailyLog.objects.filter(pk=pk, attachment__user=request.user).first()
    if log:
        log.is_deleted = True
        log.save()
        messages.success(request, 'Log entry moved to archive.')
    else:
        messages.error(request, 'Log not found.')
    return redirect('logs_list')


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
    unit = str(item.get('unit_sub_department', '')).strip()[:100]
    summary = str(item.get('activity_summary', '')).strip()
    if not unit:
        raise ValueError('Unit is required.')
    if not summary:
        raise ValueError('Activity summary is required.')
    log_id = _to_int(item.get('id'))
    if log_id:
        updated = DailyLog.objects.filter(id=log_id, attachment=session).update(
            date=log_date,
            week_number=(log_date - session.start_date).days // 7 + 1,
            unit_sub_department=unit,
            activity_summary=summary,
            tags=_clean_tags(item.get('tags')),
            numeric_tallies=_clean_tallies(item.get('numeric_tallies')),
            department_details=_clean_details(item.get('department_details')),
            skills_acquired=str(item.get('skills_acquired', '')).strip(),
            challenges_faced=str(item.get('challenges_faced', '')).strip(),
            solutions_applied=str(item.get('solutions_applied', '')).strip(),
        )
        if updated:
            return
    DailyLog.objects.create(
        attachment=session,
        date=log_date,
        week_number=(log_date - session.start_date).days // 7 + 1,
        unit_sub_department=unit,
        activity_summary=summary,
        tags=_clean_tags(item.get('tags')),
        numeric_tallies=_clean_tallies(item.get('numeric_tallies')),
        department_details=_clean_details(item.get('department_details')),
        skills_acquired=str(item.get('skills_acquired', '')).strip(),
        challenges_faced=str(item.get('challenges_faced', '')).strip(),
        solutions_applied=str(item.get('solutions_applied', '')).strip(),
        created_offline=bool(item.get('created_offline')),
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


# ==========================================
# AI CHAT — conversation-based + VISION
# ==========================================
# The actual SDK is only imported when an AI request comes in
# (via `.ai_client` inside the function body). This keeps Gunicorn
# worker startup light and prevents OOM crashes on Render's free tier.

@login_required
@require_POST
def ai_chat(request):
    if not _ai_is_configured():
        return JsonResponse({'success': False, 'error': 'AI is not configured.'}, status=500)

    try:
        data = json.loads(request.body)
        user_message = data.get('message', '').strip()
        session_id = data.get('session_id')

        image_b64  = data.get('image')
        image_mime = data.get('image_mime') or 'image/jpeg'
        image_name = (data.get('image_name') or 'file')[:80]

        if not user_message and not image_b64:
            return JsonResponse({'success': False, 'error': 'Message or image is required.'}, status=400)

        user = request.user

        if session_id:
            chat_session = AIChatSession.objects.filter(id=session_id, user=user).first()
            if not chat_session:
                return JsonResponse({'success': False, 'error': 'Chat session not found.'}, status=404)
        else:
            chat_session = AIChatSession.objects.create(user=user)

        title_text = user_message if user_message else f"Image: {image_name}"
        if not chat_session.title:
            chat_session.title = title_text[:80]
        chat_session.save(update_fields=['title', 'updated_at'])

        stored_user_content = user_message
        if image_b64:
            stored_user_content = f"[Image attached: {image_name}]\n{user_message}".strip()

        AIChatMessage.objects.create(
            session=chat_session, user=user, role='user', content=stored_user_content,
        )

        # ── Compact context: last 30 logs only, trimmed fields ───────────
        active_session = _get_active_session(request)
        context = "STUDENT PROFILE:\n"
        context += f"Name: {user.get_full_name() or user.username}\n"
        context += f"Index Number: {user.index_number}\n"
        context += f"Department: {user.get_department_display()}\n"
        context += f"Level: {user.level}\n"

        if active_session:
            context += "\nCURRENT ATTACHMENT:\n"
            context += f"Facility: {active_session.facility.name}\n"
            context += f"Dates: {active_session.start_date.strftime('%d %B %Y')} to {active_session.end_date.strftime('%d %B %Y')}\n"
            context += f"Supervisor: {active_session.assigned_supervisor_name or 'Not specified'}\n"

            logs_qs = active_session.daily_logs.filter(is_deleted=False).order_by('-date')
            total_logs = logs_qs.count()
            context += f"Total Logs Recorded: {total_logs}\n\n"

            # Only the most recent 30 to keep the prompt light and fast
            recent_logs = list(logs_qs[:30])
            if recent_logs:
                context += f"RECENT ACTIVITIES (latest {len(recent_logs)}):\n"
                for log in recent_logs:
                    context += f"- {log.date.strftime('%d %b %Y')} | {log.unit_sub_department}: {log.activity_summary[:200]}\n"
                    if log.skills_acquired.strip():
                        context += f"  Skills: {log.skills_acquired[:150]}\n"
                    if log.challenges_faced.strip():
                        context += f"  Challenges: {log.challenges_faced[:150]}\n"

            workshops = active_session.workshops.all()[:10]
            if workshops:
                context += "\nWORKSHOPS ATTENDED:\n"
                for w in workshops:
                    context += f"- {w.date.strftime('%d %b %Y')}: {w.activity[:120]} ({w.type})\n"

            competencies = active_session.competencies.all()[:15]
            if competencies:
                context += "\nCOMPETENCY AREAS (target skills):\n"
                for c in competencies:
                    context += f"- {c.text[:150]}\n"
        else:
            context += "\n(No active attachment found.)\n"

        # Only last 10 prior messages (was 20), each trimmed to 300 chars
        prior = chat_session.messages.exclude(role='').order_by('-created_at')[:10]
        if prior:
            context += "\nPREVIOUS MESSAGES IN THIS CHAT:\n"
            for m in reversed(list(prior)):
                label = "You" if m.role == 'user' else "Assistant"
                clean = m.content.replace('[Image attached:', '[Image:').split(']')[-1].strip()
                context += f"{label}: {clean[:300]}\n"
            context += "\n"

        # ── Strict output format rules (kills dash lines) ───────────────
        format_rules = (
            "OUTPUT FORMAT (STRICT — follow exactly):\n"
            "1. Do NOT use horizontal rules or section dividers of any kind.\n"
            "   Never output ---, ***, ___, ===, or any line of dashes/underscores.\n"
            "2. Do NOT echo the 'USER DATA CONTEXT' header or any '--- ... ---' marker.\n"
            "3. Use short Markdown headings: ### Heading (3 hashes max).\n"
            "4. Use bullet points with a single '* ' prefix. No nested bullets.\n"
            "5. Keep paragraphs short (2-4 sentences). Leave one blank line between sections.\n"
            "6. Use **bold** for emphasis. Never bold entire sentences.\n"
            "7. No emojis unless the user used emojis first.\n\n"
        )

        if image_b64:
            system_prompt = (
                "You are the MedPracticum Studio AI Assistant, an expert academic advisor.\n"
                "The user uploaded an image/document and asked a question.\n\n"
                + format_rules +
                "PRIORITY: Analyze the uploaded image first (describe, transcribe, or answer from it).\n"
                "Do not give a generic summary of the user's logs unless they explicitly ask.\n\n"
                f"User Question: {user_message}\n"
            )
        else:
            system_prompt = (
                "You are the MedPracticum Studio AI Assistant, an expert academic advisor for "
                "health students at Yamfo College of Health.\n\n"
                "GOAL: Help the user understand their data and write their practicum reports.\n\n"
                + format_rules +
                "CONTENT RULES:\n"
                "- When drafting report sections, use formal academic tone.\n"
                "- When asked about data, give exact numbers from the context.\n"
                "  If only recent activities are listed, mention that they are the latest entries.\n"
                "- Prioritize the listed competency areas; each chapter should support at least one.\n"
                "- Be encouraging, concise, and professional.\n\n"
                "USER DATA CONTEXT (do not repeat or rephrase this header):\n"
                f"{context}\n\n"
                f"User Question: {user_message}\n"
            )

        raw_reply = None
        if image_b64:
            from .ai_client import get_ai_reply_with_image
            try:
                image_bytes = base64.b64decode(image_b64)
                raw_reply = get_ai_reply_with_image(system_prompt, image_bytes, mime=image_mime)
            except Exception as vision_exc:
                print(f"Vision OCR Error: {vision_exc}")
                raw_reply = None
        else:
            from .ai_client import get_ai_reply
            raw_reply = get_ai_reply(system_prompt)

        if raw_reply:
            AIChatMessage.objects.create(
                session=chat_session, user=user, role='assistant', content=raw_reply,
            )
            reply = raw_reply
        else:
            if image_b64:
                reply = "I couldn't read that image right now — the vision AI is temporarily unavailable. Please try typing your question instead."
            else:
                reply = "I couldn't generate a response. Please try again in a moment."

        return JsonResponse({
            'success': True,
            'reply': reply,
            'session_id': chat_session.id,
        })

    except Exception as e:
        print(f"AI Error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
def ai_chat_sessions(request):
    sessions = (
        AIChatSession.objects
        .filter(user=request.user)
        .prefetch_related('messages')
        .order_by('-updated_at')[:50]
    )
    data = []
    for s in sessions:
        last_msg = s.messages.order_by('-created_at').first()
        msg_count = s.messages.count()
        preview_text = last_msg.content.replace('[Image attached:', '').split(']')[-1].strip() if last_msg else ''
        data.append({
            'id': s.id,
            'title': s.title or 'Untitled chat',
            'preview': (preview_text[:80] + '…') if preview_text and len(preview_text) > 80 else preview_text,
            'updated_at': s.updated_at.isoformat(),
            'updated_at_display': timezone.localtime(s.updated_at).strftime('%d %b %Y, %H:%M'),
            'message_count': msg_count,
        })
    return JsonResponse({'sessions': data})


@login_required
def ai_chat_session_detail(request, pk):
    session = AIChatSession.objects.filter(pk=pk, user=request.user).first()
    if not session:
        return JsonResponse({'error': 'Not found'}, status=404)
    messages = [
        {
            'id': m.id,
            'role': m.role,
            'content': m.content,
            'created_at_display': timezone.localtime(m.created_at).strftime('%d %b %Y, %H:%M'),
        }
        for m in session.messages.order_by('created_at')
    ]
    return JsonResponse({
        'session_id': session.id,
        'title': session.title or 'Untitled chat',
        'messages': messages,
    })


@login_required
@require_POST
def ai_chat_delete(request, pk):
    AIChatSession.objects.filter(pk=pk, user=request.user).delete()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def ai_chat_delete_all(request):
    """Delete ALL conversations for the current user."""
    AIChatSession.objects.filter(user=request.user).delete()
    return JsonResponse({'ok': True})