from django.urls import path

from . import views

urlpatterns = [
    path('generate/', views.generate_report, name='generate_report'),
    path('download/', views.download_report, name='download_report'),
    path('preview/', views.preview_report, name='preview_report'),
]