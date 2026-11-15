import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from django.utils.html import strip_tags
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from .forms import (
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    ProfileForm,
    RegistrationForm,
)
from .models import User

logger = logging.getLogger(__name__)


# ============================================================
# Registration
# ============================================================

def register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('dashboard')
    else:
        form = RegistrationForm()

    return render(request, 'accounts/register.html', {'form': form})


# ============================================================
# Profile page
# ============================================================

@login_required
def profile(request):
    """View + edit your own profile. Username and index number stay read-only."""
    if request.method == 'POST':
        form = ProfileForm(request.POST, instance=request.user, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Your profile has been updated.')
            return redirect('profile')
    else:
        form = ProfileForm(instance=request.user, user=request.user)

    return render(request, 'accounts/profile.html', {'form': form})


# ============================================================
# Contact provider page
# ============================================================

def contact(request):
    """Contact page with Call + WhatsApp buttons for support staff."""
    return render(request, 'accounts/contact.html')


# ============================================================
# Password reset — email link only
# ============================================================

def _school_context(request):
    """Fetch school branding for the reset email (logo, name, motto)."""
    ctx = {
        'school_name': 'Yamfo College of Health (COHY)',
        'school_motto': '',
        'school_logo_url': None,
    }
    try:
        from apps.reports.models import SchoolProfile
        school = SchoolProfile.objects.first()
        if school:
            ctx['school_name'] = school.name or ctx['school_name']
            ctx['school_motto'] = school.motto or ''
            if school.logo:
                ctx['school_logo_url'] = request.build_absolute_uri(school.logo.url)
    except Exception:
        pass
    return ctx


def _send_email_reset(request, user):
    """Send branded reset-link email. Returns True on success, False on failure."""
    if not user.email:
        logger.warning('Cannot send reset email: %s has no email on file.', user.username)
        return False

    # Django's default_token_generator: single-use, auto-invalidated after
    # password change, expires based on PASSWORD_RESET_TIMEOUT setting (3 days).
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    path = reverse('password_reset_confirm', kwargs={'uidb64': uidb64, 'token': token})
    reset_url = request.build_absolute_uri(path)

    ctx = {'user': user, 'reset_url': reset_url, **_school_context(request)}
    try:
        subject = 'Reset your MedPracticum Studio password'
        html_body = render_to_string('accounts/password_reset_email.html', ctx)
        msg = EmailMultiAlternatives(
            subject=subject,
            body=strip_tags(html_body),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )
        msg.attach_alternative(html_body, 'text/html')
        msg.send(fail_silently=False)
        logger.info('Password reset email sent to %s', user.email)
        return True
    except Exception:
        logger.exception('Failed to send reset email to %s', user.email)
        return False


def password_reset_request(request):
    """Step 1 — user enters their email; we email a branded reset link."""
    SAME_MESSAGE = (
        'If an account matches that email, we\u2019ve sent a password reset link '
        'to your inbox. Check your spam folder if it doesn\u2019t arrive within a few minutes.'
    )

    if request.method == 'POST':
        form = PasswordResetRequestForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email'].strip()
            user = User.objects.filter(email__iexact=email).first()
            if user and user.email:
                _send_email_reset(request, user)
            # Enumeration-safe: same message whether or not the account exists.
            messages.success(request, SAME_MESSAGE)
            return redirect('password_reset_request')
    else:
        form = PasswordResetRequestForm()

    return render(request, 'accounts/password_reset_request.html', {'form': form})


def password_reset_confirm(request, uidb64, token):
    """Step 2 — user arrives from the emailed link; sets a new password."""
    try:
        user_pk = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=user_pk)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    # Validate the token — invalid if already used, expired, or tampered with.
    if user is None or not default_token_generator.check_token(user, token):
        messages.error(
            request,
            'This reset link is invalid or has expired. Please request a new one.',
        )
        return redirect('password_reset_request')

    if request.method == 'POST':
        form = PasswordResetConfirmForm(request.POST)
        if form.is_valid():
            user.set_password(form.cleaned_data['new_password'])
            user.save()
            messages.success(
                request,
                'Your password has been reset. Please log in with your new password.',
            )
            return redirect('login')
    else:
        form = PasswordResetConfirmForm()

    return render(request, 'accounts/password_reset_confirm.html', {
        'form': form,
        'user_display': user.get_short_name() or user.username,
    })