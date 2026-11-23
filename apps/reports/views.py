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


# ✅ PHASE 3 — single helper for "post-generation" redirects.
# Adds ?attachment_id=<id>&generated=1 so options.html / pptx_options.html
# can skip the auto-show overlay after a POST build.
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
    return request.user.attachments.filter(is_deleted=False).order_by('-start_date', '-id').first()


def _build(request, session, use_ai=True, force_ai=False):
    report, _ = GeneratedReport.objects.get_or_create(attachment=session)

    if force_ai:
        report.content = {}
        report.save(update_fields=['content'])

    if use_ai and not report.content:
        try:
            from .ai_drafting import draft_sections_with_ai
            samples = list(ReportFormatSample.objects.filter(department=request.user.department))
            # ✅ PHASE 3: pass the user's preference verbatim. None = natural
            # length. The AI computes its word budget from log volume instead
            # of being forced to 25 pages (which caused padding / false notes).
            target_pages = report.options.get('target_pages')
            drafts = draft_sections_with_ai(session, samples, target_pages=target_pages)
            if drafts:
                # ✅ Extract the AI note for the UI and remove it from the saved content
                ai_note = drafts.pop('_ai_note', '')
                if ai_note:
                    messages.warning(request, ai_note)

                report.content = drafts
                report.save(update_fields=['content'])
                logger.info('AI drafted %d sections for report %s', len(drafts), report.pk)
        except Exception:
            logger.exception('AI drafting failed; continuing with template defaults')

    builder = PracticumReportBuilder(session)
    try:
        builder.save()
    except Exception as exc:
        logger.exception('Report generation failed')
        messages.error(request, f'Could not build the report: {exc}')
        return False

    # ✅ PHASE 6: rich build summary — mirrors the PPTX style so the user
    # always knows exactly what was built, what the target was, and whether
    # the AI had enough source material to reach it.
    result = builder.result
    pages = result['pages']
    target = result.get('target')
    note = (result.get('note') or '').strip()

    ai_sections = len([k for k in (report.content or {}).keys()
                       if not str(k).startswith('_')])
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

    # Choose message level: success when target hit (or unset), warning otherwise
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


# ✅ NEW: abstract preview helper (used by the report-options page overlay)
def _report_abstract_preview(session, report):
    """
    Return (paragraphs_list, keywords_str) for the abstract preview overlay
    on the report-options page. Uses saved content if present, else the
    builder's default abstract — so the preview reflects the current
    attachment even before the first generation.
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


@login_required
@require_POST
def generate_report(request):
    session, bounce = _session_with_logs(request)
    if bounce:
        return bounce
    _build(request, session, use_ai=True, force_ai=True)
    # ✅ PHASE 3 — adds generated=1 so options.html skips the overlay
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
            # ✅ PHASE 3
            return _redirect_after_generation(request, 'report_options', session)

        if 'remove_logo' in request.POST:
            if report.custom_logo:
                report.custom_logo.delete(save=False)
            report.custom_logo = None
            report.save(update_fields=['custom_logo'])
            messages.success(request, 'Custom logo removed.')
            # ✅ PHASE 3
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
            # ✅ PHASE 3
            return _redirect_after_generation(request, 'report_options', session)

        if request.POST.get('action') == 'generate':
            form = ReportOptionsForm(request.POST)
            if form.is_valid():
                cleaned_data = form.cleaned_data
                # ✅ PHASE 3: no fallback to MIN_PAGES_GUIDELINE. An empty
                # target means "natural length" — normalize_options() coerces
                # '' / None / invalid to None.
                report.options = normalize_options(cleaned_data)
                report.save(update_fields=['options'])
                _build(request, session, use_ai=True, force_ai=True)
                # ✅ PHASE 3
                return _redirect_after_generation(request, 'report_options', session)
            else:
                messages.error(request, "Your custom settings couldn't be saved. Please check the form for errors below.")
        else:
            # ✅ PHASE 3: the official form no longer has an
            # `official_target_pages` input. We preserve whatever target the
            # user previously saved via the custom form (or None for natural
            # length) — that is the "preferred pages" the AI must respect.
            existing_opts = dict(report.options or {})
            opts = normalize_options({})
            opts['include_signoff'] = False
            saved_target = existing_opts.get('target_pages')
            if saved_target:
                opts['target_pages'] = saved_target
            report.options = opts
            report.save(update_fields=['options'])
            _build(request, session, use_ai=True, force_ai=True)
            # ✅ PHASE 3
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

    # ✅ NEW: abstract preview (feeds the on-load overlay in options.html)
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
        # ✅ NEW context keys for the abstract preview overlay:
        'abstract_paragraphs': abstract_paragraphs,
        'preview_keywords': preview_keywords,
        # ✅ PHASE 3 — pass to template so JS can decide overlay visibility
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
            # ✅ PHASE 3
            return _redirect_after_generation(request, 'report_options', session)

        form = ReportContentForm(request.POST)
        if form.is_valid():
            # ✅ MERGE instead of REPLACE — preserves AI-drafted abstract,
            # keywords, acknowledgments, references (not shown in the form
            # but still consumed by the options-page preview overlay and
            # by report.html's optional sections). The 10 body-section
            # fields the form owns are still fully overwritten on every save.
            merged = dict(report.content or {})
            for key, value in form.cleaned_data.items():
                v = (value or '').strip()
                if v:
                    merged[key] = v
                elif key in merged:
                    # User cleared a field → drop it so report.html falls
                    # back to the template default for that section.
                    merged.pop(key, None)
            report.content = merged
            report.save(update_fields=['content'])

            if action == 'regenerate':
                _build(request, session, use_ai=False)
                # ✅ PHASE 3
                return _redirect_after_generation(request, 'report_options', session)

            messages.success(request, 'Your text has been saved.')
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


def _pdf_response(request, inline):
    session = _get_session(request)
    report = getattr(session, 'report', None) if session else None
    if not report or not report.pdf_file:
        raise Http404('No report has been generated yet.')

    response = FileResponse(
        report.pdf_file.open('rb'),
        as_attachment=not inline,
        filename=os.path.basename(report.pdf_file.name),
        content_type='application/pdf',
    )
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response


@login_required
def download_report(request):
    return _pdf_response(request, inline=False)


@login_required
def download_docx(request):
    session = _get_session(request)
    report = getattr(session, 'report', None) if session else None
    if not report or not report.docx_file:
        raise Http404('No Word document has been generated yet.')

    response = FileResponse(
        report.docx_file.open('rb'),
        as_attachment=True,
        filename=os.path.basename(report.docx_file.name),
        content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    )
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response


@login_required
def build_pptx(request):
    session, bounce = _session_with_logs(request)
    if bounce:
        return bounce

    template_key = request.GET.get('template') or 'cohy_classic'
    if template_key not in PPTX_TEMPLATES:
        template_key = 'cohy_classic'

    builder = PracticumReportBuilder(session)
    try:
        data = builder.build_pptx(template_key)
    except Exception as exc:
        logger.exception('PPTX build failed for session %s', session.pk)
        messages.error(request, f'Could not build the presentation: {exc}')
        return redirect(f"{reverse('report_options')}?attachment_id={session.id}")

    from django.utils.text import slugify
    filename = (
        f"practicum-presentation-"
        f"{slugify(session.user.index_number or 'student')}.pptx"
    )
    response = HttpResponse(
        data,
        content_type=(
            'application/vnd.openxmlformats-officedocument'
            '.presentationml.presentation'
        ),
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response


@login_required
def download_pptx(request):
    session = _get_session(request)
    report = getattr(session, 'report', None) if session else None
    if not report or not report.pptx_file:
        raise Http404('No presentation has been generated yet.')

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


@login_required
def view_report(request):
    return _pdf_response(request, inline=True)


@login_required
def preview_report(request):
    session = _get_session(request)
    if session is None:
        raise Http404
    return HttpResponse(PracticumReportBuilder(session).render_html())


# ---------- PowerPoint generation page ----------

def _ensure_ai_content(request, session, force_ai=False):
    report, _ = GeneratedReport.objects.get_or_create(attachment=session)

    if force_ai:
        report.content = {}
        report.save(update_fields=['content'])

    if report.content:
        return True

    try:
        from .ai_drafting import draft_sections_with_ai
        samples = list(ReportFormatSample.objects.filter(department=request.user.department))
        # ✅ PHASE 3: same as _build — pass the user's preference through
        # verbatim. None means "natural length".
        target_pages = report.options.get('target_pages')
        drafts = draft_sections_with_ai(session, samples, target_pages=target_pages)
        if drafts:
            # ✅ Extract the AI note for the UI and remove it from the saved content
            ai_note = drafts.pop('_ai_note', '')
            if ai_note:
                messages.warning(request, ai_note)

            report.content = drafts
            report.save(update_fields=['content'])
            return True
    except Exception:
        logger.exception('AI drafting failed during PPTX generation')
    return False


def _build_pptx_from_scratch(request, session, template_key, force_ai=False):
    """
    Build the .pptx + PDF preview, then report back to the user with an
    accurate summary of what was actually built (vs what was selected),
    and whether the AI draft succeeded for every section.
    """
    _ensure_ai_content(request, session, force_ai=force_ai)
    try:
        builder = PracticumReportBuilder(session)
        builder.save_pptx(template_key, force_ai=force_ai)

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


def _apply_pptx_options(report, cleaned):
    """
    ✅ FIX v3: persist PptxOptionsForm data into report.pptx_options,
    keeping the same nested {'sections': {...}} shape the builder reads.

    ✅ PHASE 7: now also persists the two font-family dropdowns. Empty
    string means "use the template's built-in font" — the builder's
    _apply_font_overrides() treats it as a no-op.
    """
    report.pptx_options = {
        # ✅ PHASE 7 — body font size range expanded 10–44 pt
        'font_size': int(cleaned.get('font_size') or 18),
        'max_bullets': int(cleaned.get('max_bullets') or 6),
        'max_chars_per_bullet': int(cleaned.get('max_chars_per_bullet') or 220),
        'include_speaker_notes': bool(cleaned.get('include_speaker_notes')),
        # ✅ PHASE 7 — font overrides
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
            # ✅ PHASE 7: official build resets the font overrides + size,
            # so a leftover Arial@40 choice from a previous custom build
            # can't leak into what should be a standard COHY look.
            # Other options (max_bullets, sections, speaker notes) are kept.
            existing = dict(report.pptx_options or {})
            existing['font_size'] = 18
            existing['heading_font_family'] = ''
            existing['body_font_family'] = ''
            report.pptx_options = existing
            report.save(update_fields=['pptx_options'])
            _build_pptx_from_scratch(request, session, 'cohy_classic', force_ai=False)
            # ✅ PHASE 3
            return _redirect_after_generation(request, 'pptx_options', session)

        if action == 'build_custom':
            # ✅ FIX v3: honor the full custom form, not just the template key
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
            # ✅ PHASE 3
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
            # ✅ PHASE 3
            return _redirect_after_generation(request, 'pptx_options', session)

    selected = request.GET.get('template') or report.pptx_template or 'cohy_classic'
    if selected not in PPTX_TEMPLATES:
        selected = 'cohy_classic'

    # ✅ FIX v3: pre-fill the custom form from saved pptx_options (or defaults)
    saved = dict(report.pptx_options or {})
    sections = saved.pop('sections', {}) or {}
    initial = {
        'pptx_template': selected,
        # ✅ NEW: pre-select the saved (or default) body font size
        'font_size': saved.get('font_size', 18),
        'max_bullets': saved.get('max_bullets', 6),
        'max_chars_per_bullet': saved.get('max_chars_per_bullet', 220),
        'include_speaker_notes': saved.get('include_speaker_notes', True),
        # ✅ PHASE 7 — pre-select saved font overrides (default = "template default")
        'heading_font_family': saved.get('heading_font_family', ''),
        'body_font_family':    saved.get('body_font_family', ''),
    }
    # Merge section toggles — default True if never set
    for f in PPTX_SECTION_FIELDS:
        initial[f] = sections.get(f, True)

    custom_form = PptxOptionsForm(initial=initial)

    all_attachments = request.user.attachments.filter(
        is_deleted=False
    ).order_by('-start_date', '-id')

    # ✅ PHASE 7: surface overflow warnings from the last build so the
    # amber banner on the template actually renders something.
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
        # ✅ PHASE 3 — pass to template so JS can decide overlay visibility
        'generated': request.GET.get('generated') == '1',
        # ✅ PHASE 7 — feeds the "Some slides may have cut-off text" banner
        'overflow_warnings': overflow_warnings,
    })


# ---------- ✅ FIX v3 — Post-generation presentation editing ----------

@login_required
def pptx_content(request):
    """
    Edit slide bullets and speaker notes after the first PPTX build.
    Mirrors report_content exactly, but for the deck.
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
            # ✅ PHASE 3
            return _redirect_after_generation(request, 'pptx_options', session)

        form = PptxContentForm(request.POST)
        if form.is_valid():
            report.pptx_content = {
                key: value.strip()
                for key, value in form.cleaned_data.items()
                if value.strip()
            }
            report.save(update_fields=['pptx_content'])

            if action == 'regenerate':
                template_key = report.pptx_template or 'cohy_classic'
                _build_pptx_from_scratch(request, session, template_key, force_ai=False)
                # ✅ PHASE 3
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


@login_required
def view_pptx_pdf(request):
    session = _get_session(request)
    report = getattr(session, 'report', None) if session else None
    if not report or not report.pptx_pdf_file:
        raise Http404('No presentation has been generated yet.')

    response = FileResponse(
        report.pptx_pdf_file.open('rb'),
        as_attachment=False,
        filename=os.path.basename(report.pptx_pdf_file.name),
        content_type='application/pdf',
    )
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response


@login_required
def download_pptx_pdf(request):
    session = _get_session(request)
    report = getattr(session, 'report', None) if session else None
    if not report or not report.pptx_pdf_file:
        raise Http404('No presentation PDF has been generated yet.')

    response = FileResponse(
        report.pptx_pdf_file.open('rb'),
        as_attachment=True,
        filename=os.path.basename(report.pptx_pdf_file.name),
        content_type='application/pdf',
    )
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response


# ---------- Format samples ----------

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