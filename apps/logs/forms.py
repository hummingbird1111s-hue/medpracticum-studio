from django import forms

from apps.accounts.forms import StyledFormMixin
from apps.facilities.models import Facility

from .models import AttachmentSession


class AttachmentSessionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = AttachmentSession
        fields = ['facility', 'academic_year', 'start_date', 'end_date', 'assigned_supervisor_name']
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
            'academic_year': forms.TextInput(attrs={'placeholder': 'e.g. 2025/2026'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['facility'].queryset = Facility.objects.filter(is_verified=True).order_by('name')
        self.fields['facility'].required = True