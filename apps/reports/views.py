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

logger = logging.getLogger(__name__)


# ============================================================
# Small helpers
# ============================================================

def _redirect_after_generation(request, view_name, session, **extra):
    """
    Post-generation redirect. Adds ?attachment_id=<id>&generated=1 so
    options.html / pptx_options.html can skip the auto-show overlay.
    """
    base = reverse(view_name)
    params = {'attachment_id': session.id, 'generated': '1'}
    params.update(extra)
    qs = '&'.join(f'{k}={v}' for k, v in params.items())
    return redirect(f'{base}?{qs}')


def _get_session(request):
    attachment_id = request.GET.get('attachment_id') or request.POST.get('attachment_id')
    if attachment_id:
        return request.user.attachments.filter(id=attachment_id, is_deleted=False).first()
    return request.user.attachments.filter(is_deleted=False).order_by('-start_date', '-id').first()


def _has_real_sections(content):
    """True if `content` contains at least one non-private section key."""
    if not content:
        return False
    return any(not str(k).startswith('_') for k in content.keys())


def _count_real_sections(content):
    return len([k for k in (content or {}).keys() if not str(k).startswith('_')])


def _ppt_is_real_pptx(path):
    """
    True if the file starts with the ZIP magic bytes that PowerPoint uses.
    Catches the "PDF saved as .pptx" bug — a PDF starts with b'%PDF'.
    """
    try:
        with open(path, 'rb') as f:
            header = f.read(4)
        return header.startswith(b'PK\x03\x04') or header.startswith(b'PK\x05\x06')
    except Exception:
        return False


# ============================================================
# Rebuild-from-saved-content (no AI)
# ============================================================

def _rebuild_report_files(request, session):
    """
    Rebuild the report PDF + DOCX from the saved report.content.

    Used by download/view handlers when the physical file has gone missing
    (Render's free tier wipes /media/ on every redeploy). Does NOT call
    the AI — just re-renders WeasyPrint + python-docx from what's already
    in report.content.
    """
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
    """
    Build (or rebuild) the report PDF + DOCX.

    AI re-drafting rules (v8):
      - force_ai=True             → clear content, draft fresh.
      - use_ai=True + no content  → draft fresh.
      - use_ai=True + fingerprint mismatch (new logs / workshops /
        competencies since last draft) → draft fresh.
      - use_ai=True + fingerprint matches → reuse saved content.
      - use_ai=False              → never touch content; just re-render.
    """
    report, _ = GeneratedReport.objects.get_or_create(attachment=session)

    if force_ai:
        report.content = {}
        report.save(update_fields=['content'])

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
                    ai_note = drafts.pop('_ai_note', '')
                    if ai_note:
                        messages.warning(request, ai_note)

                    report.content = drafts   # includes '_source_hash'
                    report.save(update_fields=['content'])
                    logger.info(
                        'AI drafted %d sections for report %s (fingerprint=%s)',
                        _count_real_sections(drafts), report.pk, current_fp[:8],
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
    """
    Return (paragraphs_list, keywords_str) for the abstract preview overlay
    on the report-options page. Uses saved content if present, else the
    builder's default abstract.
    """
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

        if action == 'reset_ai':
            report.content = {}
            report.save(update_fields=['content'])
            _build(request, session, use_ai=True, force_ai=True)
            messages.success(request, 'Report reset to a fresh AI draft.')
            return _redirect_after_generation(request, 'report_options', session)

        form = ReportContentForm(request.POST)
        if form.is_valid():
            # MERGE instead of REPLACE — preserves AI-drafted abstract,
            # keywords, acknowledgments, references, and the _source_hash
            # marker (needed so we don't re-draft after manual edits).
            merged = dict(report.content or {})
            for key, value in form.cleaned_data.items():
                v = (value or '').strip()
                if v:
                    merged[key] = v
                elif key in merged:
                    merged.pop(key, None)
            report.content = merged
            report.save(update_fields=['content'])

            # Rebuild the PDF+DOCX from saved content. use_ai=False → no
            # AI call, no fingerprint check, just WeasyPrint re-render.
            rebuild_ok = _build(request, session, use_ai=False)

            if action == 'regenerate':
                if rebuild_ok:
                    messages.success(request, 'Report regenerated with your edits.')
                return _redirect_after_generation(request, 'report_options', session)

            if rebuild_ok:
                messages.success(
                    request,
                    'Your text has been saved and the PDF refreshed. '
                    'Click "View / Print PDF" to see it.'
                )
            else:
                messages.warning(
                    request,
                    'Your text has been saved, but the PDF rebuild failed. '
                    'Try "Regenerate Report" or go back to the options page.'
                )
            return redirect(f"{reverse('report_content')}?attachment_id={session.id}")
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
    """
    Return a FileResponse for report.<field_name>. Raises FileNotFoundError
    when the file record exists but the file on disk does not.
    """
    file_field = getattr(report, field_name)
    return FileResponse(
        file_field.open('rb'),
        as_attachment=not inline,
        filename=filename_override or os.path.basename(file_field.name),
        content_type=content_type,
    )


def _try_serve_report_file(request, session, field_name, inline, content_type,
                            rebuild_ok_message=None, rebuild_fail_message=None):
    """
    Unified file-serving with auto-rebuild fallback (for PDF + DOCX).

    Flow:
      1. Report + file record must exist, else 404.
      2. Try to open the file and return the response (fast path).
      3. If FileNotFoundError → rebuild from report.content (no AI)
         and try again.
      4. If rebuild fails → friendly message + redirect.
    """
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
    """
    Ensure report.content has fresh AI-drafted report sections.

    v8: respects the source fingerprint. If logs / workshops /
    competencies changed since the last draft, we re-draft.
    """
    report, _ = GeneratedReport.objects.get_or_create(attachment=session)

    if force_ai:
        report.content = {}
        report.save(update_fields=['content'])

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
            ai_note = drafts.pop('_ai_note', '')
            if ai_note:
                messages.warning(request, ai_note)
            report.content = drafts
            report.save(update_fields=['content'])
            return True
    except Exception:
        logger.exception('AI drafting failed during PPTX generation')
    return False


def _ensure_pptx_content(request, session, force_ai=False):
    """
    Ensure report.pptx_content has fresh AI-drafted slide bullets.

    v8: same fingerprint mechanism as the report. When logs / workshops /
    competencies change, this re-drafts the slide bullets so the deck
    reflects the newest data.
    """
    report, _ = GeneratedReport.objects.get_or_create(attachment=session)

    if force_ai:
        report.pptx_content = {}
        report.save(update_fields=['pptx_content'])

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
            report.pptx_content = drafts   # includes '_source_hash'
            report.save(update_fields=['pptx_content'])
            logger.info(
                'PPTX AI drafted %d sections for report %s (fingerprint=%s)',
                _count_real_sections(drafts), report.pk, current_fp[:8],
            )
            return True
    except Exception:
        logger.exception('PPTX AI drafting failed')
    return False


def _build_pptx_from_scratch(request, session, template_key, force_ai=False):
    """
    Build the .pptx + PDF preview, then report back to the user with an
    accurate summary of what was actually built.

    v8: freshness is ensured BEFORE calling save_pptx(force_ai=False) —
    so services.py never needs to invoke the AI internally from this path.
    """
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
        return True
    except Exception as exc:
        logger.exception('PPTX build failed for session %s', session.pk)
        messages.error(request, f'Could not build the presentation: {exc}')
        return False


@login_required
def build_pptx(request):
    """
    Direct-download PPTX endpoint.

    v8: instead of returning whatever `builder.build_pptx()` produces,
    we ensure fresh content, build through save_pptx(), then serve
    report.pptx_file from disk — with a magic-byte check so a PDF
    accidentally stored under .pptx can never reach the user.
    """
    session, bounce = _session_with_logs(request)
    if bounce:
        return bounce

    template_key = request.GET.get('template') or 'cohy_classic'
    if template_key not in PPTX_TEMPLATES:
        template_key = 'cohy_classic'

    # Ensure fresh AI content for both the report and the slide deck.
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

    # Magic-byte guard: real PPTX files are ZIP archives (PK\x03\x04).
    # A PDF starts with b'%PDF' — that's the "cannot edit in PowerPoint"
    # bug. Fail loudly instead of shipping a corrupt .pptx.
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

    # Magic-byte guard on the download path too.
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
    """
    Persist PptxOptionsForm data into report.pptx_options, keeping the
    same nested {'sections': {...}} shape the builder reads.
    """
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
    })


# ============================================================
# Post-generation presentation editing
# ============================================================

@login_required
def pptx_content(request):
    """
    Edit slide bullets and speaker notes after the first PPTX build.
    Mirrors report_content, but for the deck.
    """
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

        if action == 'reset_ai':
            report.pptx_content = {}
            report.save(update_fields=['pptx_content'])
            template_key = report.pptx_template or 'cohy_classic'
            _build_pptx_from_scratch(request, session, template_key, force_ai=True)
            messages.success(request, 'Presentation reset to a fresh AI draft.')
            return _redirect_after_generation(request, 'pptx_options', session)

        form = PptxContentForm(request.POST)
        if form.is_valid():
            # Preserve '_'-prefixed keys (notably '_source_hash') so the
            # next build doesn't think the content is stale and overwrite
            # the user's edits.
            new_content = dict(report.pptx_content or {})
            for key, value in form.cleaned_data.items():
                v = (value or '').strip()
                if v:
                    new_content[key] = v
                elif key in new_content:
                    new_content.pop(key, None)
            report.pptx_content = new_content
            report.save(update_fields=['pptx_content'])

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
# Format samples
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