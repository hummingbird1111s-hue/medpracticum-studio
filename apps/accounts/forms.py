from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import User

INPUT_CLASSES = (
    "w-full rounded-lg border border-gray-300 px-3 py-2 "
    "focus:outline-none focus:ring-2 focus:ring-emerald-500"
)


class StyledFormMixin:
    """Adds Tailwind classes to every field."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', INPUT_CLASSES)


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


class LoginForm(StyledFormMixin, AuthenticationForm):
    pass