from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth import password_validation

from .models import User

INPUT_CLASSES = (
    "w-full rounded-lg border border-gray-300 px-3 py-2 "
    "focus:outline-none focus:ring-2 focus:ring-emerald-500"
)


def _normalize_phone(raw):
    """Keep only digits — '0244 123 456' → '0244123456'."""
    return ''.join(ch for ch in (raw or '') if ch.isdigit())


class StyledFormMixin:
    """Adds Tailwind classes to every field."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', INPUT_CLASSES)


# ============================================================
# Registration
# ============================================================

class RegistrationForm(StyledFormMixin, UserCreationForm):
    level = forms.TypedChoiceField(
        choices=[(100, 'Level 100'), (200, 'Level 200'), (300, 'Level 300'), (400, 'Level 400')],
        coerce=int,
        initial=300,
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = (
            'first_name', 'last_name', 'username', 'email',
            'index_number', 'department', 'level', 'phone_number',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['first_name'].required = True
        self.fields['last_name'].required = True
        self.fields['phone_number'].required = True
        self.fields['phone_number'].help_text = (
            'Format: 0244123456 or +233244123456.'
        )

    def clean_username(self):
        """Check for duplicate usernames case-insensitively."""
        username = self.cleaned_data.get('username')
        if username:
            cleaned_username = username.strip()
            if User.objects.filter(username__iexact=cleaned_username).exists():
                raise forms.ValidationError("This username is already taken. Please choose another.")
            return cleaned_username
        return username

    def clean_email(self):
        """Check for duplicate emails case-insensitively."""
        email = self.cleaned_data.get('email')
        if email:
            cleaned_email = email.strip()
            if User.objects.filter(email__iexact=cleaned_email).exists():
                raise forms.ValidationError("An account with this email already exists.")
            return cleaned_email
        return email

    def clean_index_number(self):
        index_number = self.cleaned_data.get('index_number')
        if index_number:
            cleaned_index = index_number.strip()
            if User.objects.filter(index_number__iexact=cleaned_index).exists():
                raise forms.ValidationError("A student with this Index Number is already registered.")
            return cleaned_index
        return index_number

    def clean_phone_number(self):
        raw = self.cleaned_data.get('phone_number') or ''
        cleaned = raw.strip()
        if not cleaned:
            raise forms.ValidationError("Phone number is required.")
        if len(_normalize_phone(cleaned)) < 10:
            raise forms.ValidationError("Enter a valid phone number (at least 10 digits).")
        if User.objects.filter(phone_number=cleaned).exists():
            raise forms.ValidationError("An account with this phone number already exists.")
        return cleaned


# ============================================================
# Login — accepts username, email, or phone (no index number)
# ============================================================

class LoginForm(StyledFormMixin, AuthenticationForm):
    username = forms.CharField(
        label="Username, Email, or Phone Number",
        widget=forms.TextInput(attrs={'autofocus': True}),
    )

    error_messages = {
        'invalid_login': (
            "Please enter a correct username, email, or phone number "
            "and password. Only the password is case-sensitive."
        ),
        'inactive': "This account is inactive.",
    }

    def clean(self):
        username = self.cleaned_data.get('username')
        password = self.cleaned_data.get('password')

        if username and password:
            # Fast path: exact username match
            if User.objects.filter(username=username).exists():
                return super().clean()

            # Try email (case-insensitive), then phone, then username (case-insensitive)
            phone = _normalize_phone(username)
            candidates = [{'email__iexact': username}]
            if phone:
                candidates.append({'phone_number': username.strip()})
                candidates.append({'phone_number': phone})
            candidates.append({'username__iexact': username})

            for lookup in candidates:
                qs = User.objects.filter(**lookup).distinct()
                if qs.count() == 1:
                    self.cleaned_data['username'] = qs.first().username
                    break

        return super().clean()


# ============================================================
# Password reset — request form (email only)
# ============================================================

class PasswordResetRequestForm(StyledFormMixin, forms.Form):
    email = forms.EmailField(
        label="Email address",
        max_length=254,
        widget=forms.EmailInput(attrs={
            'autofocus': True,
            'autocomplete': 'email',
            'placeholder': 'e.g. you@gmail.com',
        }),
        help_text="Enter the email address you registered with.",
    )


# ============================================================
# Password reset — confirm form (new password after clicking link)
# ============================================================

class PasswordResetConfirmForm(StyledFormMixin, forms.Form):
    new_password = forms.CharField(
        label="New password",
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        min_length=8,
        help_text=password_validation.password_validators_help_text_html(),
    )
    new_password_confirm = forms.CharField(
        label="Confirm new password",
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        min_length=8,
    )

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('new_password')
        p2 = cleaned.get('new_password_confirm')

        if p1 and p2 and p1 != p2:
            self.add_error('new_password_confirm', 'Passwords do not match.')

        if p1:
            try:
                password_validation.validate_password(p1)
            except forms.ValidationError as e:
                self.add_error('new_password', e)

        return cleaned


# ============================================================
# Profile form — editable fields on the user's own page
# ============================================================

class ProfileForm(StyledFormMixin, forms.ModelForm):
    """Fields the user can edit on their own profile page."""

    class Meta:
        model = User
        fields = (
            'username',                    # ✅ NEW: allow editing own username
            'first_name', 'last_name', 'email',
            'phone_number', 'level', 'department',
            'index_number',
        )

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        # ✅ NEW: friendlier help text than Django's default for the username field
        self.fields['username'].help_text = (
            'You log in with this. Letters, digits, and @/./+/-/_ only.'
        )

        self.fields['level'].widget = forms.Select(
            choices=[(100, 'Level 100'), (200, 'Level 200'),
                     (300, 'Level 300'), (400, 'Level 400')],
            attrs={'class': INPUT_CLASSES},
        )
        self.fields['level'].required = True

        self.fields['index_number'].help_text = (
            'Your student ID. Make sure it matches your school records.'
        )

        if user and not user.is_staff:
            self.fields['department'].disabled = True
            self.fields['department'].help_text = (
                'Contact an administrator if your department needs to change.'
            )

    # ✅ NEW: case-insensitive uniqueness check that excludes the current user
    def clean_username(self):
        """
        Prevent two users from claiming the same username — but exclude
        THIS user's pk so an unchanged save passes.
        """
        username = (self.cleaned_data.get('username') or '').strip()
        if not username:
            raise forms.ValidationError('Username is required.')
        qs = User.objects.filter(username__iexact=username).exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(
                'That username is already taken. Please choose another.'
            )
        return username

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip()
        if not email:
            return email
        # Exclude the current user's ID so they can save without changing their email
        qs = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(
                'That email is already used by another account. '
                'If you believe this is a mistake, contact an administrator.'
            )
        return email

    def clean_index_number(self):
        """
        Prevent two users from claiming the same index number — but exclude
        THIS user's pk so an unchanged save passes.
        """
        index_number = (self.cleaned_data.get('index_number') or '').strip()
        if not index_number:
            return index_number
        qs = User.objects.filter(index_number__iexact=index_number).exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(
                'Another account is already using this Index Number. '
                'If you believe this is a mistake, contact an administrator.'
            )
        return index_number