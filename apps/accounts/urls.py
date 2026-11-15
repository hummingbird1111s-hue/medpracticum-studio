from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views
from .forms import LoginForm

urlpatterns = [
    path(
        'login/',
        auth_views.LoginView.as_view(
            template_name='accounts/login.html',
            authentication_form=LoginForm,
            redirect_authenticated_user=True,
        ),
        name='login',
    ),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('register/', views.register, name='register'),

    # Profile
    path('profile/', views.profile, name='profile'),
    path(
        'password-change/',
        auth_views.PasswordChangeView.as_view(
            template_name='accounts/password_change.html',
            success_url=reverse_lazy('profile'),
        ),
        name='password_change',
    ),

    # Password reset — email link flow
    path('password-reset/', views.password_reset_request, name='password_reset_request'),
    path(
        'password-reset/reset/<uidb64>/<token>/',
        views.password_reset_confirm,
        name='password_reset_confirm',
    ),

    # Contact provider page
    path('contact/', views.contact, name='contact'),
]