import logging
import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from .services import PracticumReportBuilder

logger = logging.getLogger(__name__)


def _latest_session(user):
    return user.attachments.order_by('-start_date', '-id').first()


@login_required
@require_POST
def generate_report(request):
    session = _latest_session(request.user)
    if session is None:
        messages.error(request, 'Set up your attachment first.')
        return redirect('start_attachment')
    if not session.daily_logs.exists():
        messages.error(request, 'Add at least one daily log before generating a report.')
        return redirect('dashboard')

    try:
        PracticumReportBuilder(session).save()
    except Exception as exc:
        logger.exception('Report generation failed')
        messages.error(request, f'Could not build the report: {exc}')
        return redirect('dashboard')

    messages.success(request, 'Your report is ready.')
    return redirect('dashboard')


@login_required
def download_report(request):
    session = _latest_session(request.user)
    report = getattr(session, 'report', None) if session else None
    if not report or not report.pdf_file:
        raise Http404('No report has been generated yet.')
    return FileResponse(
        report.pdf_file.open('rb'),
        as_attachment=True,
        filename=os.path.basename(report.pdf_file.name),
        content_type='application/pdf',
    )


@login_required
def preview_report(request):
    """Fast HTML preview (no PDF) for checking wording and charts."""
    session = _latest_session(request.user)
    if session is None:
        raise Http404
    return HttpResponse(PracticumReportBuilder(session).render_html())