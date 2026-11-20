from django.urls import path
from . import views, pwa, competency_views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('terms/', views.terms_and_conditions, name='terms'),  # <-- ADD THIS LINE
    path('guide/', views.how_to_use, name='how_to_use'),
    path('attachment/new/', views.start_attachment, name='start_attachment'),
    path('logs/new/', views.new_log, name='new_log'),

    path('api/logs/sync/', views.sync_logs, name='sync_logs'),

    # AI CHAT — conversation-based
    path('api/ai/chat/', views.ai_chat, name='ai_chat'),
    path('api/ai/sessions/', views.ai_chat_sessions, name='ai_chat_sessions'),
    path('api/ai/sessions/<int:pk>/', views.ai_chat_session_detail, name='ai_chat_session_detail'),
    path('api/ai/sessions/<int:pk>/delete/', views.ai_chat_delete, name='ai_chat_delete'),
    path('api/ai/sessions/delete-all/', views.ai_chat_delete_all, name='ai_chat_delete_all'),

    path('attachments/', views.attachments_list, name='attachments_list'),
    path('logs/', views.logs_list, name='logs_list'),

    # 👇 NEW: Competency areas
    #    Upload MUST sit before any <int:pk> pattern for clarity, even though
    #    `<int:pk>` wouldn't match the literal "competencies" segment.
    # AFTER
    path(
    'attachments/competencies/upload-image/',
    views.upload_competency_image,
    name='upload_competency_image',
    ),
    
    path(
        'attachments/<int:session_id>/competencies/',
        competency_views.attachment_competencies,
        name='attachment_competencies',
    ),

    # Attachment & Log Deletion
    path('attachments/<int:pk>/delete/', views.delete_attachment, name='delete_attachment'),
    path('logs/<int:pk>/delete/', views.delete_log, name='delete_log'),

    # 👇 Restore Attachment from Archive
    path('attachments/<int:pk>/restore/', views.restore_attachment, name='restore_attachment'),

    path('archive/', views.archive_list, name='archive_list'),

    path('workshops/', views.workshops_list, name='workshops_list'),
    path('workshops/<int:pk>/delete/', views.delete_workshop, name='delete_workshop'),

    path('manifest.webmanifest', pwa.manifest, name='manifest'),
    path('service-worker.js', pwa.service_worker, name='service_worker'),
    path('offline/', pwa.offline, name='offline'),
]