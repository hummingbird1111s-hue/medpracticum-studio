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

from .forms import LAYOUT_FIELDS, SECTION_FIELDS, ReportContentForm, ReportOptionsForm
from .models import GeneratedReport, ReportFormatSample, SchoolProfile
from .pptx_templates import PPTX_TEMPLATES, template_list
from .services import PracticumReportBuilder
from .standards import MIN_PAGES_GUIDELINE, SCHOOL_STANDARD, normalize_options

logger = logging.getLogger(__name__)


# --- HELPER TO SWITCH ATTACHMENTS (UPDATED FOR SOFT DELETE) ---
def _get_session(request):
    """Get the requested attachment, or fallback to the latest one."""
    attachment_id = request.GET.get('attachment_id') or request.POST.get('attachment_id')
    if attachment_id:
        return request.user.attachments.filter(id=attachment_id, is_deleted=False).first()
    return request.user.attachments.filter(is_deleted=False).order_by('-start_date', '-id').first()


# --- AI-AWARE BUILD ---
def _build(request, session, use_ai=True, force_ai=False):
    """
    AI-draft on first build, then delegate to PracticumReportBuilder.
    If force_ai is True, it will clear old drafts and make the AI write fresh text.
    """
    report, _ = GeneratedReport.objects.get_or_create(attachment=session)

    # If force_ai is requested, clear the old content so the AI is forced to run
    if force_ai:
        report.content = {}
        report.save(update_fields=['content'])

    if use_ai and not report.content:
        try:
            from .ai_drafting import draft_sections_with_ai
            samples = list(ReportFormatSample.objects.filter(department=request.user.department))

            # Pass the target pages to the AI so it knows how long to write
            target_pages = report.options.get('target_pages') or MIN_PAGES_GUIDELINE

            drafts = draft_sections_with_ai(session, samples, target_pages=target_pages)
            if drafts:
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

    result = builder.result
    text = f"Your report is ready: {result['pages']} pages."
    if result.get('note'):
        text += ' ' + result['note']
    messages.success(request, text)
    return True


def _session_with_logs(request):
    session = _get_session(request)
    if session is None:
        messages.error(request, 'Set up your attachment first.')
        return None, redirect('start_attachment')

    # Check for active (non-deleted) logs only
    if not session.daily_logs.filter(is_deleted=False).exists():
        messages.error(request, 'Add at least one daily log before working on a report.')
        return None, redirect('dashboard')
    return session, None


# --- QR / VERIFICATION HELPERS ---
def _verification_token(report):
    """Signed, timestamped token for the QR verification URL."""
    return TimestampSigner().sign(f'report:{report.pk}')


def _make_qr_data_uri(url):
    """PNG QR code as a data URI. Empty string if qrcode isn't installed."""
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


@login_required
@require_POST
def generate_report(request):
    """Rebuild using the student's saved settings (or the official format if none)."""
    session, bounce = _session_with_logs(request)
    if bounce:
        return bounce
    # Force AI to run so we don't just rebuild the same old text
    _build(request, session, use_ai=True, force_ai=True)
    return redirect(f"{reverse('report_options')}?attachment_id={session.id}")


@login_required
def report_options(request):
    session, bounce = _session_with_logs(request)
    if bounce:
        return bounce

    allow_custom = getattr(settings, 'REPORT_ALLOW_CUSTOM_FORMAT', True)
    report, _ = GeneratedReport.objects.get_or_create(attachment=session)
    form = None

    if request.method == 'POST':
        # -- Cover logo: upload --
        if 'update_logo' in request.POST and request.FILES.get('custom_logo'):
            report.custom_logo = request.FILES['custom_logo']
            report.save(update_fields=['custom_logo'])
            messages.success(request, 'Cover logo updated.')
            return redirect(f"{reverse('report_options')}?attachment_id={session.id}")

        # -- Cover logo: remove --
        if 'remove_logo' in request.POST:
            if report.custom_logo:
                report.custom_logo.delete(save=False)
            report.custom_logo = None
            report.save(update_fields=['custom_logo'])
            messages.success(request, 'Custom logo removed.')
            return redirect(f"{reverse('report_options')}?attachment_id={session.id}")

        # -- PowerPoint template selection (build + save) --
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
            return redirect(f"{reverse('report_options')}?attachment_id={session.id}")

        # -- Custom format generate --
        # We ONLY do this if the action is EXACTLY 'generate'
        if request.POST.get('action') == 'generate':
            form = ReportOptionsForm(request.POST)
            if form.is_valid():
                cleaned_data = form.cleaned_data

                if not cleaned_data.get('target_pages'):
                    cleaned_data['target_pages'] = MIN_PAGES_GUIDELINE

                report.options = normalize_options(cleaned_data)
                report.save(update_fields=['options'])

                _build(request, session, use_ai=True, force_ai=True)
                return redirect(f"{reverse('report_options')}?attachment_id={session.id}")
            else:
                messages.error(request, "Your custom settings couldn't be saved. Please check the form for errors below.")
        else:
            # -- Official format generate / Regenerate --
            # This triggers if the user clicks the green button OR if any other button was clicked
            report.options = normalize_options({})

            # CRITICAL FIX: Force supervisor sign-off to be excluded by default
            report.options['include_signoff'] = False

            report.save(update_fields=['options'])

            _build(request, session, use_ai=True, force_ai=True)
            return redirect(f"{reverse('report_options')}?attachment_id={session.id}")

    if form is None and allow_custom:
        form = ReportOptionsForm(initial=normalize_options(report.options))

    school = SchoolProfile.objects.first()
    samples = ReportFormatSample.objects.filter(
        department=request.user.department
    ).order_by('position')

    all_attachments = request.user.attachments.filter(
        is_deleted=False
    ).order_by('-start_date', '-id')

    # Which PPTX template is currently selected for this report?
    selected_pptx_template = (
        request.GET.get('template')
        or report.pptx_template
        or 'cohy_classic'
    )
    if selected_pptx_template not in PPTX_TEMPLATES:
        selected_pptx_template = 'cohy_classic'

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
        # ---- PowerPoint picker ----
        'pptx_templates': template_list(),
        'selected_pptx_template': selected_pptx_template,
        'has_pptx': bool(report.pptx_file),
    })


@login_required
def report_content(request):
    """
    Edit Report Manually — locked until a PDF exists.
    Actions: save (stay) | regenerate (rebuild + back) | cancel (discard) | reset_ai
    """
    session, bounce = _session_with_logs(request)
    if bounce:
        return bounce

    report, _ = GeneratedReport.objects.get_or_create(attachment=session)

    # Gate: must have generated a report at least once
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
            # Force AI to write fresh text
            _build(request, session, use_ai=True, force_ai=True)
            messages.success(request, 'Report reset to a fresh AI draft.')
            return redirect(f"{reverse('report_options')}?attachment_id={session.id}")

        form = ReportContentForm(request.POST)
        if form.is_valid():
            # Save every non-empty field. Empty fields fall back to template defaults via the builder.
            report.content = {
                key: value.strip()
                for key, value in form.cleaned_data.items()
                if value.strip()
            }
            report.save(update_fields=['content'])

            if action == 'regenerate':
                # use_ai=False prevents AI from running and hanging. Rebuilds instantly.
                _build(request, session, use_ai=False)
                return redirect(f"{reverse('report_options')}?attachment_id={session.id}")

            messages.success(request, 'Your text has been saved.')
            return redirect(f"{reverse('report_content')}?attachment_id={session.id}")
    else:
        form = ReportContentForm(initial=builder.effective_content())

    return render(request, 'reports/content.html', {
        'form': form,
        'session': session,
        'report': report,
    })


# --- VERIFY (QR TARGET) ---
@login_required
def verify_report(request, token):
    """Supervisor-facing landing page reached by scanning the PDF QR code."""
    try:
        value = TimestampSigner().unsign(token, max_age=60 * 60 * 24 * 730)  # 2 years
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

    # Added a no-cache header so the browser always shows the newest PDF
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


# ---------- PowerPoint ----------

@login_required
def build_pptx(request):
    """
    Stream a freshly-built PPTX on demand.
    Uses ?template=<key> (or ?attachment_id=<id>) and does NOT save the file.
    """
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
    """Serve the *saved* pptx_file if one has been built."""
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
    """Opens the PDF in the browser so it can be printed."""
    return _pdf_response(request, inline=True)


@login_required
def preview_report(request):
    """Fast HTML preview (no PDF) for checking wording and charts."""
    session = _get_session(request)
    if session is None:
        raise Http404
    return HttpResponse(PracticumReportBuilder(session).render_html())


# ---------- PowerPoint generation page ----------
#
# `pptx_options` is the dedicated presentation-builder page (mirrors
# `report_options`): template gallery + live preview + build actions.
# The three helpers below keep the AI-draft logic in one place so
# both the report page and the presentation page behave consistently.

def _ensure_ai_content(request, session, force_ai=False):
    """
    Make sure report.content is populated before building the PPTX.
    Runs the AI only if content is empty, or when force_ai=True clears it first.
    Returns True if content is ready, False on AI failure.
    """
    report, _ = GeneratedReport.objects.get_or_create(attachment=session)

    if force_ai:
        report.content = {}
        report.save(update_fields=['content'])

    if report.content:
        return True

    try:
        from .ai_drafting import draft_sections_with_ai
        samples = list(ReportFormatSample.objects.filter(department=request.user.department))
        target_pages = report.options.get('target_pages') or MIN_PAGES_GUIDELINE
        drafts = draft_sections_with_ai(session, samples, target_pages=target_pages)
        if drafts:
            report.content = drafts
            report.save(update_fields=['content'])
            return True
    except Exception:
        logger.exception('AI drafting failed during PPTX generation')
    return False


def _build_pptx_from_scratch(request, session, template_key, force_ai=False):
    """
    Ensure AI content, then build and persist BOTH the .pptx and its PDF
    preview via PracticumReportBuilder.save_pptx().

    `force_ai` is forwarded to save_pptx() so a "Reset AI & Rebuild" click
    clears BOTH report.content (via _ensure_ai_content above) AND
    report.pptx_content (inside save_pptx). Without forwarding, the
    slide-specific bullets would be stale after a reset.
    """
    _ensure_ai_content(request, session, force_ai=force_ai)
    try:
        PracticumReportBuilder(session).save_pptx(template_key, force_ai=force_ai)
        messages.success(
            request,
            f'Presentation built with the "{template_key}" template.',
        )
        return True
    except Exception as exc:
        logger.exception('PPTX build failed for session %s', session.pk)
        messages.error(request, f'Could not build the presentation: {exc}')
        return False


@login_required
def pptx_options(request):
    """
    Presentation builder page — template gallery, live preview,
    official / custom build, and reset-AI-and-rebuild actions.
    """
    session, bounce = _session_with_logs(request)
    if bounce:
        return bounce

    report, _ = GeneratedReport.objects.get_or_create(attachment=session)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'build_official':
            _build_pptx_from_scratch(request, session, 'cohy_classic', force_ai=False)
            return redirect(f"{reverse('pptx_options')}?attachment_id={session.id}")

        if action == 'build_custom':
            template_key = request.POST.get('pptx_template') or 'cohy_classic'
            if template_key not in PPTX_TEMPLATES:
                template_key = 'cohy_classic'
            _build_pptx_from_scratch(request, session, template_key, force_ai=False)
            return redirect(f"{reverse('pptx_options')}?attachment_id={session.id}")

        if action == 'regenerate':
            template_key = (
                request.POST.get('pptx_template')
                or report.pptx_template
                or 'cohy_classic'
            )
            if template_key not in PPTX_TEMPLATES:
                template_key = 'cohy_classic'
            _build_pptx_from_scratch(request, session, template_key, force_ai=True)
            return redirect(f"{reverse('pptx_options')}?attachment_id={session.id}")

    selected = request.GET.get('template') or report.pptx_template or 'cohy_classic'
    if selected not in PPTX_TEMPLATES:
        selected = 'cohy_classic'

    all_attachments = request.user.attachments.filter(
        is_deleted=False
    ).order_by('-start_date', '-id')

    return render(request, 'reports/pptx_options.html', {
        'session': session,
        'report': report,
        'all_attachments': all_attachments,
        'pptx_templates': template_list(),
        'selected_pptx_template': selected,
        'has_pptx': bool(report.pptx_file),
        'has_pptx_pdf': bool(report.pptx_pdf_file),
    })


@login_required
def view_pptx_pdf(request):
    """Opens the presentation PDF inline in the browser."""
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
    """Downloads the presentation PDF."""
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


# ---------- Format samples (modal on the options page) ----------

@login_required
@require_POST
def upload_format_sample(request):
    """Stores a log book / format reference image uploaded by an admin."""
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
    """Deletes a format reference image. Staff only."""
    if not request.user.is_staff:
        return JsonResponse({'error': 'Only staff can delete samples.'}, status=403)

    ReportFormatSample.objects.filter(pk=pk).delete()
    return JsonResponse({'ok': True})