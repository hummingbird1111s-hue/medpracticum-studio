# apps/reports/views.py

import base64
import logging
import os
from io import BytesIO

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import (
    LAYOUT_FIELDS, SECTION_FIELDS,
    PPTX_SECTION_FIELDS,
    PptxContentForm, PptxOptionsForm,
    ReportContentForm, ReportOptionsForm,
)
from .models import GeneratedReport, ReportFormatSample, SchoolProfile
from .pptx_templates import PPTX_TEMPLATES, template_list
from .services import PracticumReportBuilder
from .standards import MIN_PAGES_GUIDELINE, SCHOOL_STANDARD, normalize_options

# ---- Phase 3 gate ----
from apps.payments.gating import subscription_gate
# ---- end ----

logger = logging.getLogger(__name__)


# ============================================================
# Constants
# ============================================================

_KNOWN_SECTION_KEYS = (
    'introduction',
    'facility_profile',
    'activities_performed',
    'skills_acquired',
    'workshops',
    'lessons_learnt',
    'observation',
    'challenges',
    'recommendations',
    'conclusion',
)


# ============================================================
# Small helpers
# ============================================================

def _notify_build(user, message, kind):
    """Fire a bell notification. Never raises — build must never fail on notify."""
    try:
        from apps.notifications.models import notify
        notify(user, message, kind=kind)
    except Exception:
        logger.exception('Failed to create %s notification for %s', kind, user)


def _redirect_after_generation(request, view_name, session, **extra):
    base = reverse(view_name)
    params = {'attachment_id': session.id, 'generated': '1'}
    params.update(extra)
    qs = '&'.join(f'{k}={v}' for k, v in params.items())
    return redirect(f'{base}?{qs}')


def _get_session(request):
    attachment_id = request.GET.get('attachment_id') or request.POST.get('attachment_id')
    if attachment_id:
        return request.user.attachments.filter(id=attachment_id, is_deleted=False).first()

    # No attachment_id in the URL (e.g. sidebar click). Prefer an attachment
    # that actually has daily logs, otherwise the guard in _session_with_logs()
    # bounces the user to /dashboard/ even though they have a valid older
    # attachment with logs.
    from django.db.models import Count, Q
    qs = request.user.attachments.filter(is_deleted=False).annotate(
        log_count=Count('daily_logs', filter=Q(daily_logs__is_deleted=False))
    )
    return (
        qs.filter(log_count__gt=0).order_by('-start_date', '-id').first()
        or qs.order_by('-start_date', '-id').first()
    )


def _has_real_sections(content):
    if not content:
        return False
    return any(not str(k).startswith('_') for k in content.keys())


def _count_real_sections(content):
    return len([k for k in (content or {}).keys() if not str(k).startswith('_')])


def _ppt_is_real_pptx(path):
    try:
        with open(path, 'rb') as f:
            header = f.read(4)
        return header.startswith(b'PK\x03\x04') or header.startswith(b'PK\x05\x06')
    except Exception:
        return False


# ============================================================
# ✅ Regeneration tracking helper
# ============================================================

def _save_content_with_regen_tracking(report, field_name, new_value, *, is_regeneration):
    """
    Save report.content or report.pptx_content, incrementing the matching
    regeneration counter when is_regeneration=True.

    Rules of engagement at every call site:
      • First ever write (content was empty) → is_regeneration=False
      • Any AI draft that replaces existing content → is_regeneration=True
      • User-initiated edits with no AI → is_regeneration=False
      • AI refinement / retry / explicit "Reset & Rebuild" → is_regeneration=True
    """
    regen_field = (
        'regeneration_count' if field_name == 'content'
        else 'pptx_regeneration_count'
    )
    setattr(report, field_name, new_value)
    update_fields = [field_name]
    if is_regeneration:
        setattr(report, regen_field, (getattr(report, regen_field) or 0) + 1)
        update_fields.append(regen_field)
    report.save(update_fields=update_fields)


# ============================================================
# Rebuild-from-saved-content (no AI)
# ============================================================

def _rebuild_report_files(request, session):
    try:
        builder = PracticumReportBuilder(session)
        builder.save()
        return True
    except Exception:
        logger.exception(
            'Rebuild failed for attachment %s — user will need to regenerate',
            session.pk,
        )
        return False


# ============================================================
# Report build (AI + fingerprint)
# ============================================================

def _build(request, session, use_ai=True, force_ai=False):
    report, _ = GeneratedReport.objects.get_or_create(attachment=session)

    # ✅ Capture BEFORE force_ai blanks the field, so the regen counter fires
    # correctly on a rebuild of content that already existed.
    had_real_content = _has_real_sections(report.content)

    if force_ai:
        _save_content_with_regen_tracking(report, 'content', {}, is_regeneration=False)

    if use_ai:
        from .ai_drafting import draft_sections_with_ai, source_fingerprint

        current_fp = source_fingerprint(session)
        saved_fp = (report.content or {}).get('_source_hash')

        needs_draft = (
            force_ai
            or not _has_real_sections(report.content)
            or (saved_fp != current_fp)
        )

        if needs_draft:
            try:
                samples = list(
                    ReportFormatSample.objects.filter(department=request.user.department)
                )
                target_pages = report.options.get('target_pages')
                drafts = draft_sections_with_ai(session, samples, target_pages=target_pages)
                if drafts:
                    # Phase B3: keep _ai_note / _section_status / _failed_keys
                    # inside content so the options template can render the
                    # persistent AI-status banner + retry button on reload.
                    _save_content_with_regen_tracking(
                        report, 'content', drafts,
                        is_regeneration=had_real_content,
                    )
                    logger.info(
                        'AI drafted %d sections for report %s (fingerprint=%s, regen=%s)',
                        _count_real_sections(drafts), report.pk, current_fp[:8],
                        had_real_content,
                    )
            except Exception:
                logger.exception('AI drafting failed; continuing with template defaults')

    builder = PracticumReportBuilder(session)
    try:
        builder.save()
    except Exception as exc:
        logger.exception('Report generation failed')
        messages.error(request, f'Could not build the report: {exc}')
        return False

    # ---- Rich build summary ----
    result = builder.result
    pages = result['pages']
    target = result.get('target')
    note = (result.get('note') or '').strip()

    ai_sections = _count_real_sections(report.content)
    log_count = session.daily_logs.filter(is_deleted=False).count()
    try:
        comp_count = session.competencies.count()
    except Exception:
        comp_count = 0

    summary_bits = [f"{pages} page{'s' if pages != 1 else ''}"]
    if target:
        summary_bits.append(f"target {target}")
    summary_bits.append(f"{ai_sections} AI section{'s' if ai_sections != 1 else ''}")
    summary_bits.append(f"{log_count} log{'s' if log_count != 1 else ''}")
    summary_bits.append(f"{comp_count} competenc{'y' if comp_count == 1 else 'ies'}")
    summary = " · ".join(summary_bits)

    if target and pages != target:
        if note:
            messages.warning(request, f"⚠️ Report ready: {summary}. {note}")
        else:
            messages.warning(
                request,
                f"⚠️ Report ready: {summary}. (Report length didn't match your target — "
                f"regenerate with more logs or a different target.)"
            )
    else:
        if note:
            messages.success(request, f"✅ Report ready: {summary}. {note}")
        else:
            messages.success(request, f"✅ Report ready: {summary}.")

    # ---- Bell notification ----
    _notify_build(request.user, 'Report generated successfully', 'report_generated')
    return True


def _session_with_logs(request):
    session = _get_session(request)
    if session is None:
        messages.error(request, 'Set up your attachment first.')
        return None, redirect('start_attachment')
    if not session.daily_logs.filter(is_deleted=False).exists():
        messages.error(request, 'Add at least one daily log before working on a report.')
        return None, redirect('dashboard')
    return session, None


def _verification_token(report):
    return TimestampSigner().sign(f'report:{report.pk}')


def _make_qr_data_uri(url):
    try:
        import qrcode
        img = qrcode.make(url)
        buf = BytesIO()
        img.save(buf, format='PNG')
        return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        logger.warning('qrcode lib not installed; PDF renders without QR')
        return ''
    except Exception:
        logger.exception('QR generation failed')
        return ''


def _report_abstract_preview(session, report):
    import re as _re
    try:
        builder = PracticumReportBuilder(session)
        defaults = builder.content_defaults()
        saved = (report.content or {})
        abstract_text = (
            (saved.get('abstract') or '').strip()
            or (defaults.get('abstract') or '').strip()
        )
        keywords_text = (
            (saved.get('keywords') or '').strip()
            or (defaults.get('keywords') or '').strip()
        )
        paragraphs = [
            p.strip() for p in _re.split(r'\n\s*\n', abstract_text) if p.strip()
        ]
        return paragraphs, keywords_text
    except Exception:
        logger.exception('Abstract preview build failed')
        return [], ''


# ============================================================
# Report generation views
# ============================================================

@login_required
@require_POST
def generate_report(request):
    session, bounce = _session_with_logs(request)
    if bounce:
        return bounce

    # ---- Phase 3 gate ----
    allowed, _status, gate_msg = subscription_gate(request.user)
    if not allowed:
        messages.error(request, gate_msg)
        return _redirect_after_generation(request, 'report_options', session)
    # ---- end gate ----

    _build(request, session, use_ai=True, force_ai=True)
    return _redirect_after_generation(request, 'report_options', session)


@login_required
def report_options(request):
    session, bounce = _session_with_logs(request)
    if bounce:
        return bounce

    allow_custom = getattr(settings, 'REPORT_ALLOW_CUSTOM_FORMAT', True)
    report, _ = GeneratedReport.objects.get_or_create(attachment=session)
    form = None

    if request.method == 'POST':
        # ---- Phase 3 gate ----
        if 'update_logo' not in request.POST and 'remove_logo' not in request.POST:
            allowed, _status, gate_msg = subscription_gate(request.user)
            if not allowed:
                messages.error(request, gate_msg)
                return _redirect_after_generation(request, 'report_options', session)
        # ---- end gate ----

        if 'update_logo' in request.POST and request.FILES.get('custom_logo'):
            report.custom_logo = request.FILES['custom_logo']
            report.save(update_fields=['custom_logo'])
            messages.success(request, 'Cover logo updated.')
            return _redirect_after_generation(request, 'report_options', session)

        if 'remove_logo' in request.POST:
            if report.custom_logo:
                report.custom_logo.delete(save=False)
            report.custom_logo = None
            report.save(update_fields=['custom_logo'])
            messages.success(request, 'Custom logo removed.')
            return _redirect_after_generation(request, 'report_options', session)

        if request.POST.get('action') == 'build_pptx':
            template_key = request.POST.get('pptx_template') or 'cohy_classic'
            if template_key not in PPTX_TEMPLATES:
                template_key = 'cohy_classic'
            try:
                builder = PracticumReportBuilder(session)
                builder.save_pptx(template_key)
                messages.success(request, 'Presentation built successfully.')
            except Exception as exc:
                logger.exception('PPTX build failed for session %s', session.pk)
                messages.error(request, f'Could not build the presentation: {exc}')
            return _redirect_after_generation(request, 'report_options', session)

        if request.POST.get('action') == 'generate':
            form = ReportOptionsForm(request.POST)
            if form.is_valid():
                cleaned_data = form.cleaned_data
                report.options = normalize_options(cleaned_data)
                report.save(update_fields=['options'])
                _build(request, session, use_ai=True, force_ai=True)
                return _redirect_after_generation(request, 'report_options', session)
            else:
                messages.error(
                    request,
                    "Your custom settings couldn't be saved. "
                    "Please check the form for errors below."
                )
        else:
            existing_opts = dict(report.options or {})
            opts = normalize_options({})
            opts['include_signoff'] = False
            saved_target = existing_opts.get('target_pages')
            if saved_target:
                opts['target_pages'] = saved_target
            report.options = opts
            report.save(update_fields=['options'])
            _build(request, session, use_ai=True, force_ai=True)
            return _redirect_after_generation(request, 'report_options', session)

    if form is None and allow_custom:
        form = ReportOptionsForm(initial=normalize_options(report.options))

    school = SchoolProfile.objects.first()
    samples = ReportFormatSample.objects.filter(
        department=request.user.department
    ).order_by('position')

    all_attachments = request.user.attachments.filter(
        is_deleted=False
    ).order_by('-start_date', '-id')

    selected_pptx_template = (
        request.GET.get('template')
        or report.pptx_template
        or 'cohy_classic'
    )
    if selected_pptx_template not in PPTX_TEMPLATES:
        selected_pptx_template = 'cohy_classic'

    abstract_paragraphs, preview_keywords = _report_abstract_preview(session, report)

    return render(request, 'reports/options.html', {
        'form': form,
        'layout_fields': [form[n] for n in LAYOUT_FIELDS] if form else [],
        'section_fields': [form[n] for n in SECTION_FIELDS] if form else [],
        'standard': SCHOOL_STANDARD,
        'min_pages': MIN_PAGES_GUIDELINE,
        'allow_custom': allow_custom,
        'has_logo': bool(school and school.logo),
        'school_logo': school.logo if school and school.logo else None,
        'school_format_samples': samples,
        'session': session,
        'report': report,
        'all_attachments': all_attachments,
        'can_edit': bool(report.pdf_file),
        'pptx_templates': template_list(),
        'selected_pptx_template': selected_pptx_template,
        'has_pptx': bool(report.pptx_file),
        'abstract_paragraphs': abstract_paragraphs,
        'preview_keywords': preview_keywords,
        'generated': request.GET.get('generated') == '1',

        # --- Phase B3: AI status banner + retry ---
        'ai_note': (report.content or {}).get('_ai_note', ''),
        'failed_keys': (report.content or {}).get('_failed_keys', []),
        'section_status': (report.content or {}).get('_section_status', {}),
    })


@login_required
def report_content(request):
    session, bounce = _session_with_logs(request)
    if bounce:
        return bounce

    report, _ = GeneratedReport.objects.get_or_create(attachment=session)

    if not report.pdf_file:
        messages.info(
            request,
            'Generate your report first — then you can edit the AI draft here.'
        )
        return redirect(f"{reverse('report_options')}?attachment_id={session.id}")

    builder = PracticumReportBuilder(session)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'cancel':
            return redirect(f"{reverse('report_options')}?attachment_id={session.id}")

        # ---- Phase 3 gate ----
        if action in ('reset_ai', 'regenerate'):
            allowed, _status, gate_msg = subscription_gate(request.user)
            if not allowed:
                messages.error(request, gate_msg)
                return redirect(f"{reverse('report_content')}?attachment_id={session.id}")
        # ---- end gate ----

        if action == 'reset_ai':
            # _build(force_ai=True) captures had_real_content before clearing,
            # clears content, re-drafts, and increments the regen counter.
            _build(request, session, use_ai=True, force_ai=True)
            messages.success(request, 'Report reset to a fresh AI draft.')
            return _redirect_after_generation(request, 'report_options', session)

        form = ReportContentForm(request.POST)
        if form.is_valid():
            merged = dict(report.content or {})

            for key, value in form.cleaned_data.items():
                if key not in request.POST:
                    continue
                v = (value or '').strip()
                if v:
                    merged[key] = v
                elif key in merged:
                    merged.pop(key, None)

            for section_key in _KNOWN_SECTION_KEYS:
                if section_key not in request.POST:
                    continue
                if section_key in form.cleaned_data:
                    continue
                v = (request.POST.get(section_key) or '').strip()
                if v:
                    merged[section_key] = v
                elif section_key in merged:
                    merged.pop(section_key, None)

            # Plain user edits — NOT a regeneration
            _save_content_with_regen_tracking(
                report, 'content', merged, is_regeneration=False,
            )

            if action == 'regenerate':
                try:
                    from .ai_drafting import refine_user_content
                    refined = refine_user_content(session, merged)

                    if refined and refined != merged:
                        # AI actually produced new text — this IS a regeneration
                        _save_content_with_regen_tracking(
                            report, 'content', refined, is_regeneration=True,
                        )
                        messages.success(
                            request,
                            '✨ Your edits were refined into academic English '
                            'and saved. Every fact, unit, and bullet was preserved.'
                        )
                    else:
                        messages.info(
                            request,
                            '✨ AI refinement made no changes — your text '
                            'was already polished.'
                        )
                except Exception:
                    logger.exception(
                        'AI refinement failed for report %s — keeping raw edits',
                        report.pk,
                    )
                    messages.warning(
                        request,
                        'AI refinement failed, but your exact edits were saved. '
                        'The PDF was rebuilt with your text as typed.'
                    )

            rebuild_ok = _build(request, session, use_ai=False)

            if not rebuild_ok:
                messages.warning(
                    request,
                    'Your text was saved, but the PDF rebuild failed. '
                    'Try again from the options page.'
                )
                return redirect(f"{reverse('report_content')}?attachment_id={session.id}")

            if action == 'save':
                messages.success(
                    request,
                    '✅ Your exact edits are saved and the PDF was rebuilt.'
                )

            return _redirect_after_generation(request, 'report_options', session)

        else:
            messages.error(
                request,
                "Your edits couldn't be saved — please check the form for errors."
            )
    else:
        form = ReportContentForm(initial=builder.effective_content())

    return render(request, 'reports/content.html', {
        'form': form,
        'session': session,
        'report': report,
    })


@login_required
def verify_report(request, token):
    try:
        value = TimestampSigner().unsign(token, max_age=60 * 60 * 24 * 730)
        report_pk = int(value.split(':')[1])
    except (BadSignature, SignatureExpired, ValueError, IndexError):
        raise Http404('Invalid or expired verification link.')

    report = get_object_or_404(
        GeneratedReport.objects.select_related('attachment'),
        pk=report_pk,
    )
    session = report.attachment

    if request.method == 'POST' and request.POST.get('action') == 'approve':
        report.is_signed_off = True
        report.supervisor_signature_date = timezone.now()
        report.save(update_fields=['is_signed_off', 'supervisor_signature_date'])
        messages.success(request, 'Report approved and signed off.')
        return redirect(f"{reverse('report_options')}?attachment_id={session.id}")

    logs = session.daily_logs.filter(is_deleted=False).order_by('date')

    return render(request, 'reports/verify.html', {
        'report': report,
        'session': session,
        'logs': logs,
        'student_name': session.user.get_username(),
        'facility_name': session.facility.name if session.facility else '',
    })


# ============================================================
# File serving helpers
# ============================================================

def _serve_file(report, field_name, inline, content_type, filename_override=None):
    file_field = getattr(report, field_name)
    return FileResponse(
        file_field.open('rb'),
        as_attachment=not inline,
        filename=filename_override or os.path.basename(file_field.name),
        content_type=content_type,
    )


def _try_serve_report_file(request, session, field_name, inline, content_type,
                            rebuild_ok_message=None, rebuild_fail_message=None):
    report = getattr(session, 'report', None) if session else None
    if not report or not getattr(report, field_name):
        raise Http404('No report file has been generated yet.')

    try:
        response = _serve_file(report, field_name, inline, content_type)
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        return response
    except FileNotFoundError:
        logger.warning(
            'Report file missing on disk: report=%s field=%s — rebuilding',
            report.pk, field_name,
        )

    rebuilt = _rebuild_report_files(request, session)
    if rebuilt:
        if rebuild_ok_message:
            messages.info(request, rebuild_ok_message)
        report.refresh_from_db()
        try:
            response = _serve_file(report, field_name, inline, content_type)
            response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            return response
        except FileNotFoundError:
            logger.error(
                'Rebuild succeeded but file still missing for report=%s field=%s',
                report.pk, field_name,
            )

    messages.error(
        request,
        rebuild_fail_message or (
            'The file is missing and could not be rebuilt. '
            'Go back to the options page and click "Generate Official Report".'
        )
    )
    return redirect(f"{reverse('report_options')}?attachment_id={session.id}")


def _pdf_response(request, inline):
    session = _get_session(request)
    return _try_serve_report_file(
        request, session,
        field_name='pdf_file',
        inline=inline,
        content_type='application/pdf',
        rebuild_ok_message=(
            'Your PDF was rebuilt from your saved content. '
            '(The server clears temporary files on every redeploy; '
            'this rebuild is a one-time cost per deploy.)'
        ),
        rebuild_fail_message=(
            'The PDF file is missing and could not be rebuilt automatically. '
            'Click "Generate Official Report" on the options page to rebuild it.'
        ),
    )


@login_required
def download_report(request):
    return _pdf_response(request, inline=False)


@login_required
def download_docx(request):
    session = _get_session(request)
    return _try_serve_report_file(
        request, session,
        field_name='docx_file',
        inline=False,
        content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        rebuild_ok_message=(
            'Your Word document was rebuilt from your saved content.'
        ),
        rebuild_fail_message=(
            'The Word file is missing and could not be rebuilt. '
            'Click "Generate Official Report" to rebuild from scratch.'
        ),
    )


@login_required
def view_report(request):
    return _pdf_response(request, inline=True)


@login_required
def preview_report(request):
    session = _get_session(request)
    if session is None:
        raise Http404
    return HttpResponse(PracticumReportBuilder(session).render_html())


# ============================================================
# PowerPoint — AI content, build, and download
# ============================================================

def _ensure_ai_content(request, session, force_ai=False):
    report, _ = GeneratedReport.objects.get_or_create(attachment=session)

    # ✅ Capture BEFORE force_ai blanks the field
    had_real_content = _has_real_sections(report.content)

    if force_ai:
        _save_content_with_regen_tracking(report, 'content', {}, is_regeneration=False)

    from .ai_drafting import draft_sections_with_ai, source_fingerprint

    current_fp = source_fingerprint(session)
    saved_fp = (report.content or {}).get('_source_hash')

    needs_draft = (
        force_ai
        or not _has_real_sections(report.content)
        or (saved_fp != current_fp)
    )
    if not needs_draft:
        return True

    try:
        samples = list(
            ReportFormatSample.objects.filter(department=request.user.department)
        )
        target_pages = report.options.get('target_pages')
        drafts = draft_sections_with_ai(session, samples, target_pages=target_pages)
        if drafts:
            # Phase B3: keep the metadata keys — the template needs them.
            _save_content_with_regen_tracking(
                report, 'content', drafts,
                is_regeneration=had_real_content,
            )
            return True
    except Exception:
        logger.exception('AI drafting failed during PPTX generation')
    return False


def _ensure_pptx_content(request, session, force_ai=False):
    report, _ = GeneratedReport.objects.get_or_create(attachment=session)

    # ✅ Capture BEFORE force_ai blanks the field
    had_pptx_content = _has_real_sections(report.pptx_content)

    if force_ai:
        _save_content_with_regen_tracking(report, 'pptx_content', {}, is_regeneration=False)

    from .ai_drafting import draft_presentation_with_ai, source_fingerprint

    current_fp = source_fingerprint(session)
    saved_fp = (report.pptx_content or {}).get('_source_hash')

    needs_draft = (
        force_ai
        or not _has_real_sections(report.pptx_content)
        or (saved_fp != current_fp)
    )
    if not needs_draft:
        return True

    try:
        drafts = draft_presentation_with_ai(session)
        if drafts:
            _save_content_with_regen_tracking(
                report, 'pptx_content', drafts,
                is_regeneration=had_pptx_content,
            )
            logger.info(
                'PPTX AI drafted %d sections for report %s (fingerprint=%s, regen=%s)',
                _count_real_sections(drafts), report.pk, current_fp[:8],
                had_pptx_content,
            )
            return True
    except Exception:
        logger.exception('PPTX AI drafting failed')
    return False


# ============================================================
# Phase B3 — retry helpers and views
# ============================================================

def _recompute_ai_note_from_status(section_status, section_label='section'):
    """Rebuild a human-readable summary from the merged status dict."""
    if not section_status:
        return ''
    ai_keys = [k for k, v in section_status.items() if v == 'ai']
    local_keys = [k for k, v in section_status.items() if v == 'local']
    total = len(section_status)
    plural = 's' if total != 1 else ''
    if not local_keys:
        return f'All {total} {section_label}{plural} were drafted by AI.'
    if not ai_keys:
        return (
            f'All {total} {section_label}{plural} were drafted locally '
            f'because the AI service was unavailable. The output is '
            f'complete and usable. Wait 60 seconds and retry to get '
            f'AI-polished text.'
        )
    local_plural = '' if len(local_keys) == 1 else 's'
    return (
        f'{len(ai_keys)} of {total} {section_label}{plural} were drafted by AI. '
        f'{len(local_keys)} {section_label}{local_plural} were drafted locally: '
        f'{", ".join(local_keys)}. You can retry just those after 60 seconds.'
    )


@login_required
@require_POST
def retry_report_sections(request):
    """Re-draft a specific set of report sections that were drafted locally."""
    session, bounce = _session_with_logs(request)
    if bounce:
        return bounce

    allowed, _status, gate_msg = subscription_gate(request.user)
    if not allowed:
        messages.error(request, gate_msg)
        return _redirect_after_generation(request, 'report_options', session)

    retry_keys = request.POST.getlist('keys')
    if not retry_keys:
        messages.error(request, 'No sections were selected for retry.')
        return _redirect_after_generation(request, 'report_options', session)

    report, _ = GeneratedReport.objects.get_or_create(attachment=session)

    from .ai_drafting import draft_sections_with_ai, source_fingerprint

    try:
        fresh = draft_sections_with_ai(session, retry_keys=retry_keys)
    except Exception:
        logger.exception('Retry drafting failed for keys=%s', retry_keys)
        messages.error(
            request,
            'Retry failed unexpectedly. Wait 60 seconds and try again.',
        )
        return _redirect_after_generation(request, 'report_options', session)

    if not fresh:
        messages.error(
            request,
            'Retry produced no content. Please wait 60 seconds and try again.',
        )
        return _redirect_after_generation(request, 'report_options', session)

    # Merge new section text back into the stored content
    merged = dict(report.content or {})
    for key in retry_keys:
        if key in fresh and fresh[key]:
            merged[key] = fresh[key]

    # Merge status: new status for retried keys, old status for the rest
    old_status = dict(merged.get('_section_status') or {})
    new_status = dict(fresh.get('_section_status') or {})
    old_status.update(new_status)
    merged['_section_status'] = old_status

    # Recompute failed keys: drop keys we just retried, then add any new
    # failures the fresh call reported.
    old_failed = list(merged.get('_failed_keys') or [])
    new_failed = list(fresh.get('_failed_keys') or [])
    remaining = [k for k in old_failed if k not in retry_keys]
    merged['_failed_keys'] = list(dict.fromkeys(remaining + new_failed))
    merged['_ai_note'] = _recompute_ai_note_from_status(old_status, 'section')
    merged['_source_hash'] = source_fingerprint(session)

    # ✅ A retry is always a regeneration action
    _save_content_with_regen_tracking(report, 'content', merged, is_regeneration=True)

    # Rebuild the PDF from the new content
    try:
        builder = PracticumReportBuilder(session)
        builder.save()
        if new_failed:
            messages.warning(
                request,
                f'Retry finished: {len(new_failed)} of {len(retry_keys)} '
                f'section(s) are still locally drafted (AI unavailable). '
                f'Wait 60 seconds and retry again if you wish.',
            )
        else:
            messages.success(
                request,
                f'Retry finished: all {len(retry_keys)} section(s) are now '
                f'AI-drafted and the PDF was rebuilt.',
            )
    except Exception:
        logger.exception('PDF rebuild failed after retry')
        messages.warning(
            request,
            'AI retry succeeded but the PDF rebuild failed. '
            'The new text is saved — try downloading the report again.',
        )

    return _redirect_after_generation(request, 'report_options', session)


@login_required
@require_POST
def retry_pptx_sections(request):
    """Re-draft a specific set of PPTX slides that were drafted locally."""
    session, bounce = _session_with_logs(request)
    if bounce:
        return bounce

    allowed, _status, gate_msg = subscription_gate(request.user)
    if not allowed:
        messages.error(request, gate_msg)
        return _redirect_after_generation(request, 'pptx_options', session)

    retry_keys = request.POST.getlist('keys')
    if not retry_keys:
        messages.error(request, 'No slides were selected for retry.')
        return _redirect_after_generation(request, 'pptx_options', session)

    report, _ = GeneratedReport.objects.get_or_create(attachment=session)

    from .ai_drafting import draft_presentation_with_ai, source_fingerprint

    try:
        fresh = draft_presentation_with_ai(session, retry_keys=retry_keys)
    except Exception:
        logger.exception('PPTX retry drafting failed for keys=%s', retry_keys)
        messages.error(
            request,
            'Retry failed unexpectedly. Wait 60 seconds and try again.',
        )
        return _redirect_after_generation(request, 'pptx_options', session)

    if not fresh:
        messages.error(
            request,
            'Retry produced no content. Please wait 60 seconds and try again.',
        )
        return _redirect_after_generation(request, 'pptx_options', session)

    merged = dict(report.pptx_content or {})
    for key in retry_keys:
        if key in fresh and fresh[key]:
            merged[key] = fresh[key]

    old_status = dict(merged.get('_pptx_section_status') or {})
    new_status = dict(fresh.get('_pptx_section_status') or {})
    old_status.update(new_status)
    merged['_pptx_section_status'] = old_status

    old_failed = list(merged.get('_pptx_failed_keys') or [])
    new_failed = list(fresh.get('_pptx_failed_keys') or [])
    remaining = [k for k in old_failed if k not in retry_keys]
    merged['_pptx_failed_keys'] = list(dict.fromkeys(remaining + new_failed))
    merged['_pptx_ai_note'] = _recompute_ai_note_from_status(old_status, 'slide')
    merged['_source_hash'] = source_fingerprint(session)

    # ✅ A retry is always a regeneration action
    _save_content_with_regen_tracking(report, 'pptx_content', merged, is_regeneration=True)

    # Rebuild the PPTX file
    template_key = report.pptx_template or 'cohy_classic'
    try:
        builder = PracticumReportBuilder(session)
        builder.save_pptx(template_key, force_ai=False)
        if new_failed:
            messages.warning(
                request,
                f'Retry finished: {len(new_failed)} of {len(retry_keys)} '
                f'slide(s) are still locally drafted. Wait 60 seconds and '
                f'retry again if you wish.',
            )
        else:
            messages.success(
                request,
                f'Retry finished: all {len(retry_keys)} slide(s) are now '
                f'AI-drafted and the presentation was rebuilt.',
            )
    except Exception:
        logger.exception('PPTX rebuild failed after retry')
        messages.warning(
            request,
            'AI retry succeeded but the presentation rebuild failed. '
            'The new text is saved — try regenerating again.',
        )

    return _redirect_after_generation(request, 'pptx_options', session)


def _build_pptx_from_scratch(request, session, template_key, force_ai=False):
    _ensure_ai_content(request, session, force_ai=force_ai)
    _ensure_pptx_content(request, session, force_ai=force_ai)

    try:
        builder = PracticumReportBuilder(session)
        builder.save_pptx(template_key, force_ai=False)

        summary = getattr(builder, '_last_pptx_summary', None)
        if summary:
            built = summary['slides_built']
            selected = summary['slides_selected'] or built
            fallbacks = summary['ai_fallback_count']
            tpl = summary.get('template', template_key)
            plural = 's' if built != 1 else ''

            if fallbacks == 0:
                messages.success(
                    request,
                    f"✅ Presentation ready: {built} slide{plural} · Template: {tpl}"
                )
            else:
                fb_plural = 's' if fallbacks != 1 else ''
                messages.warning(
                    request,
                    f"⚠️ Presentation ready: {built} slide{plural} · Template: {tpl} · "
                    f"Note: {fallbacks} slide{fb_plural} used fallback content "
                    f"(AI draft incomplete — using trimmed report text)"
                )
        else:
            messages.success(
                request,
                f'Presentation built with the "{template_key}" template.'
            )

        # ---- Bell notification ----
        _notify_build(request.user, 'Presentation generated successfully', 'pptx_generated')
        return True
    except Exception as exc:
        logger.exception('PPTX build failed for session %s', session.pk)
        messages.error(request, f'Could not build the presentation: {exc}')
        return False


@login_required
def build_pptx(request):
    session, bounce = _session_with_logs(request)
    if bounce:
        return bounce

    # ---- Phase 3 gate ----
    allowed, _status, gate_msg = subscription_gate(request.user)
    if not allowed:
        messages.error(request, gate_msg)
        return redirect(f"{reverse('pptx_options')}?attachment_id={session.id}")
    # ---- end gate ----

    template_key = request.GET.get('template') or 'cohy_classic'
    if template_key not in PPTX_TEMPLATES:
        template_key = 'cohy_classic'

    _ensure_ai_content(request, session, force_ai=False)
    _ensure_pptx_content(request, session, force_ai=False)

    try:
        builder = PracticumReportBuilder(session)
        builder.save_pptx(template_key, force_ai=False)
    except Exception as exc:
        logger.exception('PPTX build failed for session %s', session.pk)
        messages.error(request, f'Could not build the presentation: {exc}')
        return redirect(f"{reverse('pptx_options')}?attachment_id={session.id}")

    report = GeneratedReport.objects.get(attachment=session)
    if not report.pptx_file:
        messages.error(request, 'The presentation file was not written to disk.')
        return redirect(f"{reverse('pptx_options')}?attachment_id={session.id}")

    try:
        path = report.pptx_file.path
    except Exception:
        path = None
    if path and not _ppt_is_real_pptx(path):
        logger.error(
            'PPTX magic-byte check failed for report %s (file=%s). '
            'The file is not a real .pptx — likely a PDF was saved with a .pptx name.',
            report.pk, path,
        )
        messages.error(
            request,
            'The presentation file appears to be corrupt (not a valid .pptx). '
            'Try "Regenerate" on the presentations page. If the problem '
            'persists, the build pipeline is misconfigured and needs a fix.'
        )
        return redirect(f"{reverse('pptx_options')}?attachment_id={session.id}")

    try:
        from django.utils.text import slugify
        filename = (
            f"practicum-presentation-"
            f"{slugify(session.user.index_number or 'student')}.pptx"
        )
        response = FileResponse(
            report.pptx_file.open('rb'),
            as_attachment=True,
            filename=filename,
            content_type=(
                'application/vnd.openxmlformats-officedocument'
                '.presentationml.presentation'
            ),
        )
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        return response
    except FileNotFoundError:
        logger.warning('PPTX missing after build for report %s', report.pk)
        messages.warning(
            request,
            'The presentation file was cleared when the server redeployed. '
            'Go to the presentations page and click "Regenerate" to rebuild it.'
        )
        return redirect(f"{reverse('pptx_options')}?attachment_id={session.id}")


@login_required
def download_pptx(request):
    session = _get_session(request)
    report = getattr(session, 'report', None) if session else None
    if not report or not report.pptx_file:
        raise Http404('No presentation has been generated yet.')

    try:
        path = report.pptx_file.path
    except Exception:
        path = None
    if path and not _ppt_is_real_pptx(path):
        logger.error(
            'PPTX download magic-byte check failed for report %s (file=%s).',
            report.pk, path,
        )
        messages.error(
            request,
            'The presentation file appears to be corrupt (not a valid .pptx). '
            'Go to the presentations page and click "Regenerate".'
        )
        return redirect(f"{reverse('pptx_options')}?attachment_id={session.id}")

    try:
        response = FileResponse(
            report.pptx_file.open('rb'),
            as_attachment=True,
            filename=os.path.basename(report.pptx_file.name),
            content_type=(
                'application/vnd.openxmlformats-officedocument'
                '.presentationml.presentation'
            ),
        )
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        return response
    except FileNotFoundError:
        logger.warning('PPTX missing for report %s', report.pk)
        messages.warning(
            request,
            'The presentation file was cleared when the server redeployed. '
            'Go to the presentations page and click "Regenerate" to rebuild it.'
        )
        return redirect(f"{reverse('pptx_options')}?attachment_id={session.id}")


@login_required
def view_pptx_pdf(request):
    session = _get_session(request)
    report = getattr(session, 'report', None) if session else None
    if not report or not report.pptx_pdf_file:
        raise Http404('No presentation has been generated yet.')

    try:
        response = FileResponse(
            report.pptx_pdf_file.open('rb'),
            as_attachment=False,
            filename=os.path.basename(report.pptx_pdf_file.name),
            content_type='application/pdf',
        )
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        return response
    except FileNotFoundError:
        logger.warning('PPTX PDF missing for report %s', report.pk)
        messages.warning(
            request,
            'The presentation PDF was cleared when the server redeployed. '
            'Go to the presentations page and click "Regenerate" to rebuild it.'
        )
        return redirect(f"{reverse('pptx_options')}?attachment_id={session.id}")


@login_required
def download_pptx_pdf(request):
    session = _get_session(request)
    report = getattr(session, 'report', None) if session else None
    if not report or not report.pptx_pdf_file:
        raise Http404('No presentation PDF has been generated yet.')

    try:
        response = FileResponse(
            report.pptx_pdf_file.open('rb'),
            as_attachment=True,
            filename=os.path.basename(report.pptx_pdf_file.name),
            content_type='application/pdf',
        )
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        return response
    except FileNotFoundError:
        logger.warning('PPTX PDF missing for report %s (download)', report.pk)
        messages.warning(
            request,
            'The presentation PDF was cleared when the server redeployed. '
            'Go to the presentations page and click "Regenerate" to rebuild it.'
        )
        return redirect(f"{reverse('pptx_options')}?attachment_id={session.id}")


def _apply_pptx_options(report, cleaned):
    report.pptx_options = {
        'font_size': int(cleaned.get('font_size') or 18),
        'max_bullets': int(cleaned.get('max_bullets') or 6),
        'max_chars_per_bullet': int(cleaned.get('max_chars_per_bullet') or 220),
        'include_speaker_notes': bool(cleaned.get('include_speaker_notes')),
        'heading_font_family': (cleaned.get('heading_font_family') or '').strip(),
        'body_font_family':    (cleaned.get('body_font_family')    or '').strip(),
        'sections': {f: bool(cleaned.get(f)) for f in PPTX_SECTION_FIELDS},
    }
    report.save(update_fields=['pptx_options'])


@login_required
def pptx_options(request):
    session, bounce = _session_with_logs(request)
    if bounce:
        return bounce

    report, _ = GeneratedReport.objects.get_or_create(attachment=session)

    if request.method == 'POST':
        action = request.POST.get('action')

        # ---- Phase 3 gate ----
        if action in ('build_official', 'build_custom', 'regenerate'):
            allowed, _status, gate_msg = subscription_gate(request.user)
            if not allowed:
                messages.error(request, gate_msg)
                return _redirect_after_generation(request, 'pptx_options', session)
        # ---- end gate ----

        if action == 'build_official':
            existing = dict(report.pptx_options or {})
            existing['font_size'] = 18
            existing['heading_font_family'] = ''
            existing['body_font_family'] = ''
            report.pptx_options = existing
            report.save(update_fields=['pptx_options'])
            _build_pptx_from_scratch(request, session, 'cohy_classic', force_ai=False)
            return _redirect_after_generation(request, 'pptx_options', session)

        if action == 'build_custom':
            form = PptxOptionsForm(request.POST)
            if form.is_valid():
                _apply_pptx_options(report, form.cleaned_data)
                template_key = form.cleaned_data.get('pptx_template') or 'cohy_classic'
                if template_key not in PPTX_TEMPLATES:
                    template_key = 'cohy_classic'
                _build_pptx_from_scratch(request, session, template_key, force_ai=False)
            else:
                messages.error(
                    request,
                    "Your custom presentation settings couldn't be saved. "
                    "Check the form for errors below.",
                )
            return _redirect_after_generation(request, 'pptx_options', session)

        if action == 'regenerate':
            template_key = (
                request.POST.get('pptx_template')
                or report.pptx_template
                or 'cohy_classic'
            )
            if template_key not in PPTX_TEMPLATES:
                template_key = 'cohy_classic'
            _build_pptx_from_scratch(request, session, template_key, force_ai=True)
            return _redirect_after_generation(request, 'pptx_options', session)

    selected = request.GET.get('template') or report.pptx_template or 'cohy_classic'
    if selected not in PPTX_TEMPLATES:
        selected = 'cohy_classic'

    saved = dict(report.pptx_options or {})
    sections = saved.pop('sections', {}) or {}
    initial = {
        'pptx_template': selected,
        'font_size': saved.get('font_size', 18),
        'max_bullets': saved.get('max_bullets', 6),
        'max_chars_per_bullet': saved.get('max_chars_per_bullet', 220),
        'include_speaker_notes': saved.get('include_speaker_notes', True),
        'heading_font_family': saved.get('heading_font_family', ''),
        'body_font_family':    saved.get('body_font_family', ''),
    }
    for f in PPTX_SECTION_FIELDS:
        initial[f] = sections.get(f, True)

    custom_form = PptxOptionsForm(initial=initial)

    all_attachments = request.user.attachments.filter(
        is_deleted=False
    ).order_by('-start_date', '-id')

    overflow_warnings = (report.pptx_options or {}).get('last_overflow_warnings') or []

    return render(request, 'reports/pptx_options.html', {
        'session': session,
        'report': report,
        'all_attachments': all_attachments,
        'pptx_templates': template_list(),
        'selected_pptx_template': selected,
        'has_pptx': bool(report.pptx_file),
        'has_pptx_pdf': bool(report.pptx_pdf_file),
        'custom_form': custom_form,
        'pptx_section_fields': PPTX_SECTION_FIELDS,
        'generated': request.GET.get('generated') == '1',
        'overflow_warnings': overflow_warnings,

        # --- Phase B3: PPTX status banner + retry ---
        'pptx_ai_note': (report.pptx_content or {}).get('_pptx_ai_note', ''),
        'pptx_failed_keys': (report.pptx_content or {}).get('_pptx_failed_keys', []),
        'pptx_section_status': (report.pptx_content or {}).get('_pptx_section_status', {}),
    })


# ============================================================
# Post-generation presentation editing
# ============================================================

@login_required
def pptx_content(request):
    session, bounce = _session_with_logs(request)
    if bounce:
        return bounce

    report, _ = GeneratedReport.objects.get_or_create(attachment=session)

    if not report.pptx_file:
        messages.info(
            request,
            'Build your presentation first — then you can edit the slide text here.'
        )
        return redirect(f"{reverse('pptx_options')}?attachment_id={session.id}")

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'cancel':
            return redirect(f"{reverse('pptx_options')}?attachment_id={session.id}")

        # ---- Phase 3 gate ----
        if action in ('reset_ai', 'regenerate'):
            allowed, _status, gate_msg = subscription_gate(request.user)
            if not allowed:
                messages.error(request, gate_msg)
                return redirect(f"{reverse('pptx_content')}?attachment_id={session.id}")
        # ---- end gate ----

        if action == 'reset_ai':
            # _build_pptx_from_scratch(force_ai=True) → _ensure_pptx_content
            # captures had_content BEFORE clearing, then increments correctly.
            template_key = report.pptx_template or 'cohy_classic'
            _build_pptx_from_scratch(request, session, template_key, force_ai=True)
            messages.success(request, 'Presentation reset to a fresh AI draft.')
            return _redirect_after_generation(request, 'pptx_options', session)

        form = PptxContentForm(request.POST)
        if form.is_valid():
            new_content = dict(report.pptx_content or {})
            for key, value in form.cleaned_data.items():
                v = (value or '').strip()
                if v:
                    new_content[key] = v
                elif key in new_content:
                    new_content.pop(key, None)
            # Plain user edits — NOT a regeneration
            _save_content_with_regen_tracking(
                report, 'pptx_content', new_content, is_regeneration=False,
            )

            if action == 'regenerate':
                template_key = report.pptx_template or 'cohy_classic'
                _build_pptx_from_scratch(request, session, template_key, force_ai=False)
                return _redirect_after_generation(request, 'pptx_options', session)

            messages.success(request, 'Your slide text has been saved.')
            return redirect(f"{reverse('pptx_content')}?attachment_id={session.id}")
    else:
        existing = report.pptx_content or {}
        initial = {}
        for key in (
            'pptx_intro_bullets', 'pptx_facility_bullets', 'pptx_activities_bullets',
            'pptx_skills_bullets', 'pptx_challenges_bullets',
            'pptx_observation_recommendation_bullets', 'pptx_conclusion_bullets',
            'pptx_speaker_notes',
        ):
            initial[key] = existing.get(key, '')
        form = PptxContentForm(initial=initial)

    return render(request, 'reports/pptx_content.html', {
        'form': form,
        'session': session,
        'report': report,
    })


# ============================================================
# Format samples (staff-only endpoints)
# ============================================================

@login_required
@require_POST
def upload_format_sample(request):
    if not request.user.is_staff:
        return JsonResponse({'error': 'Only staff can upload samples.'}, status=403)

    image = request.FILES.get('image')
    if not image:
        return JsonResponse({'error': 'No image provided.'}, status=400)

    caption = request.POST.get('caption', '').strip()[:200]
    style_notes = request.POST.get('style_notes', '').strip()
    dept = request.POST.get('department') or request.user.department

    last_position = (
        ReportFormatSample.objects.filter(department=dept)
        .order_by('-position')
        .values_list('position', flat=True)
        .first()
    ) or 0

    sample = ReportFormatSample.objects.create(
        department=dept,
        image=image,
        caption=caption,
        style_notes=style_notes,
        position=last_position + 1,
    )
    return JsonResponse({
        'id': sample.pk,
        'url': sample.image.url,
        'caption': sample.caption,
        'style_notes': sample.style_notes,
        'department': sample.department,
    })


@login_required
@require_POST
def delete_format_sample(request, pk):
    if not request.user.is_staff:
        return JsonResponse({'error': 'Only staff can delete samples.'}, status=403)
    ReportFormatSample.objects.filter(pk=pk).delete()
    return JsonResponse({'ok': True})