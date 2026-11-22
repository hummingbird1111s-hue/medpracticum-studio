from django.urls import path

from . import views

urlpatterns = [
    path('', views.subscribe, name='subscribe'),
    path('paystack/callback/', views.paystack_callback, name='paystack_callback'),
    path('activate-test/', views.activate_test, name='subscribe_activate_test'),
]