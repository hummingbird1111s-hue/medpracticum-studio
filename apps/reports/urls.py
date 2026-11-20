# apps/reports/urls.py

from django.urls import path

from . import views

urlpatterns = [
    # ---------- Report (PDF / Word) ----------
    path('options/',             views.report_options,   name='report_options'),
    path('content/',             views.report_content,   name='report_content'),
    path('generate/',            views.generate_report,  name='generate_report'),
    path('download/docx/',       views.download_docx,    name='download_docx'),
    path('download/',            views.download_report,  name='download_report'),
    path('view/',                views.view_report,      name='view_report'),
    path('preview/',             views.preview_report,   name='preview_report'),

    # ---------- Format sample upload / delete (modal) ----------
    path('format-sample/upload/',          views.upload_format_sample, name='upload_format_sample'),
    path('format-sample/<int:pk>/delete/', views.delete_format_sample, name='delete_format_sample'),

    # ---------- PowerPoint ----------
    path('pptx/',              views.pptx_options,      name='pptx_options'),
    # ✅ FIX v3: post-generation slide editing
    path('pptx/content/',      views.pptx_content,      name='pptx_content'),
    path('pptx/view-pdf/',     views.view_pptx_pdf,     name='view_pptx_pdf'),
    path('pptx/download-pdf/', views.download_pptx_pdf, name='download_pptx_pdf'),
    path('build-pptx/',        views.build_pptx,        name='build_pptx'),
    path('download-pptx/',     views.download_pptx,     name='download_pptx'),

    # ---------- QR verification (supervisor-facing) ----------
    path('verify/<str:token>/', views.verify_report, name='verify_report'),
]