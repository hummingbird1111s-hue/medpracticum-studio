from django.urls import path

from . import views

urlpatterns = [
    path('', views.subscribe, name='subscribe'),
    path('activate-test/', views.activate_test, name='subscribe_activate_test'),
]