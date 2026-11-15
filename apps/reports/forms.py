from django import forms

from apps.accounts.forms import StyledFormMixin

from .standards import (
    FONT_FAMILIES, FONT_SIZES, LINE_SPACINGS, MARGINS, MAX_TARGET, MIN_TARGET,
)

CHECKBOX_CLASSES = 'h-4 w-4 rounded border-gray-300 text-emerald-600'

LAYOUT_FIELDS = ['font_family', 'font_size', 'line_spacing', 'margin', 'target_pages']
SECTION_FIELDS = [
    'page_numbers', 'logo_on_title', 'logo_in_header',
    'include_declaration', 'include_acknowledgments', 'include_abstract', 'include_toc',
    'include_charts', 'include_skills', 'include_references', 'include_appendix', 'include_signoff',
]


class ReportOptionsForm(StyledFormMixin, forms.Form):
    font_family = forms.ChoiceField(choices=[(n, n) for n in FONT_FAMILIES], label='Font')
    font_size = forms.TypedChoiceField(
        choices=[(s, f'{s} pt') for s in FONT_SIZES], coerce=int, label='Font size',
    )
    line_spacing = forms.TypedChoiceField(
        choices=[(v, f'{v:g}') for v in LINE_SPACINGS], coerce=float, label='Line spacing',
        help_text='If you set a page target below, this is only the starting point.',
    )
    margin = forms.TypedChoiceField(
        choices=[(v, f'{v:g} inch') for v in MARGINS], coerce=float, label='Page margins',
    )
    target_pages = forms.IntegerField(
        required=False, min_value=MIN_TARGET, max_value=MAX_TARGET,
        label='Target number of pages (optional)',
        help_text='Any number from 1 upwards. Leave blank for the natural length. We adjust spacing first, '
                  'and for small targets we also trim optional sections. We never add filler text.',
    )

    page_numbers = forms.BooleanField(required=False, label='Page numbers')
    logo_on_title = forms.BooleanField(required=False, label='School logo on the title page')
    logo_in_header = forms.BooleanField(required=False, label='School logo in the header of every page')
    include_declaration = forms.BooleanField(required=False, label='Declaration page')
    include_acknowledgments = forms.BooleanField(required=False, label='Acknowledgments')
    include_abstract = forms.BooleanField(required=False, label='Abstract')
    include_toc = forms.BooleanField(required=False, label='Table of contents')
    include_charts = forms.BooleanField(required=False, label='Charts in Chapter 3')
    include_skills = forms.BooleanField(required=False, label='Skills acquired section')
    include_references = forms.BooleanField(required=False, label='References page')
    include_appendix = forms.BooleanField(required=False, label='Appendix A: full daily log record')
    include_signoff = forms.BooleanField(required=False, label='Supervisor sign-off page with QR code')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in SECTION_FIELDS:
            self.fields[name].widget.attrs['class'] = CHECKBOX_CLASSES


class ReportContentForm(StyledFormMixin, forms.Form):
    abstract = forms.CharField(
        required=False, label='Abstract', widget=forms.Textarea(attrs={'rows': 8}),
        help_text='About 150 to 250 words summarising the attachment.',
    )
    keywords = forms.CharField(required=False, label='Keywords', help_text='Separate with commas.')
    acknowledgments = forms.CharField(
        required=False, label='Acknowledgments', widget=forms.Textarea(attrs={'rows': 8}),
        help_text='Separate paragraphs with a blank line.',
    )
    conclusion = forms.CharField(
        required=False, label='Conclusion (Chapter 5)', widget=forms.Textarea(attrs={'rows': 8}),
        help_text='Separate paragraphs with a blank line.',
    )
    facility_recs = forms.CharField(
        required=False, label='Recommendations to the facility', widget=forms.Textarea(attrs={'rows': 5}),
        help_text='One recommendation per line.',
    )
    academic_recs = forms.CharField(
        required=False, label='Recommendations to the college', widget=forms.Textarea(attrs={'rows': 5}),
        help_text='One recommendation per line.',
    )
    references = forms.CharField(
        required=False, label='References (APA 7th edition)', widget=forms.Textarea(attrs={'rows': 8}),
        help_text='One reference per line, in alphabetical order. Leave blank if you cited no sources.',
    )