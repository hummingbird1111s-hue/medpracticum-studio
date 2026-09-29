from django.urls import path

from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('attachment/new/', views.start_attachment, name='start_attachment'),
    path('logs/new/', views.new_log, name='new_log'),
    path('api/logs/sync/', views.sync_logs, name='sync_logs'),
]