from django import forms

from apps.accounts.forms import StyledFormMixin
from apps.facilities.models import Facility

from .models import AttachmentSession


class AttachmentSessionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = AttachmentSession
        fields = [
            'facility',
            'academic_year',
            'start_date',
            'end_date',
            'assigned_supervisor_name',
            'report_title',
            'course_code',
            'semester',
        ]
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
            'academic_year': forms.TextInput(attrs={'placeholder': 'e.g. 2025/2026'}),
            'report_title': forms.TextInput(
                attrs={'placeholder': 'e.g. VACATION PRACTICUM I REPORT'}
            ),
            'course_code': forms.TextInput(attrs={'placeholder': 'e.g. BIHM 115'}),
        }
        labels = {
            'report_title': 'Report title',
            'course_code': 'Course code',
            'semester': 'Semester',
        }
        help_texts = {
            'report_title': 'Appears on the cover page of your report. You can change it later.',
            'course_code': 'Your course code for this practicum, as shown on your log book.',
            'semester': 'Which semester does this attachment cover?',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['facility'].queryset = Facility.objects.filter(is_verified=True).order_by('name')
        self.fields['facility'].required = True
        # Report metadata is optional — the report will fall back to sensible defaults if blank.
        self.fields['report_title'].required = False
        self.fields['course_code'].required = False
        self.fields['semester'].required = False