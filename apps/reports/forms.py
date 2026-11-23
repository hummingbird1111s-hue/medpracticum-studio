from django import forms

from apps.accounts.forms import StyledFormMixin

from .pptx_templates import PPTX_TEMPLATES
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

# ✅ FIX v3: presentation-specific section toggles
PPTX_SECTION_FIELDS = [
    'slide_cover', 'slide_title',
    'slide_intro', 'slide_facility', 'slide_activities',
    'slide_skills', 'slide_challenges', 'slide_observation',
    'slide_conclusion', 'slide_thankyou',
]

# ✅ PHASE 7: font family choices for the two new PPTX dropdowns.
# The empty-string option means "use the template's built-in font".
# The builder's _apply_font_overrides() treats any non-empty value as a
# hard override — so picking "Calibri" or "Georgia" always wins over the
# template default, and picking "Template default" defers to the design.
PPTX_FONT_CHOICES = [
    ('',                'Template default'),
    ('Times New Roman', 'Times New Roman'),
    ('Arial',           'Arial'),
    ('Calibri',         'Calibri'),
    ('Cambria',         'Cambria'),
    ('Georgia',         'Georgia'),
    ('Trebuchet MS',    'Trebuchet MS'),
    ('Verdana',         'Verdana'),
    ('Century Gothic',  'Century Gothic'),
    ('Courier New',     'Courier New'),
    ('Garamond',        'Garamond'),
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
    """
    ✅ Edit-Report-Manually form — exactly the 10 body sections that
    report.html renders, in the same order (Introduction → Conclusion).
    Field names MUST match the content keys read by report.html
    (`content.introduction`, `content.facility_profile`, …).

    Removed vs the previous version:
      • abstract, keywords            — not rendered in the PDF (Phase 2
                                        removed the abstract page). The
                                        preview overlay still reads the
                                        AI-drafted abstract from
                                        report.content directly.
      • acknowledgments               — not rendered in the PDF.
      • references                    — not rendered in the PDF.
      • facility_recs, academic_recs  — dead keys; the Recommendations
                                        chapter reads content.recommendations.

    Kept in report.content (untouched by this form): abstract, keywords,
    acknowledgments, references — merged-on-save by report_content view
    so future overlays / rebuilds still find the AI's original prose.
    """

    introduction = forms.CharField(
        required=False,
        label='Introduction (Chapter 1)',
        widget=forms.Textarea(attrs={'rows': 10}),
        help_text='Separate paragraphs with a blank line.',
    )
    facility_profile = forms.CharField(
        required=False,
        label='Facility Profile (Chapter 2)',
        widget=forms.Textarea(attrs={'rows': 10}),
        help_text='Write in THIRD person (e.g. "The hospital has 150 beds" — '
                  'avoid "I" / "we"). First-person text here is rejected and '
                  'the facility record is used instead.',
    )
    activities_performed = forms.CharField(
        required=False,
        label='Activities Performed (Chapter 3)',
        widget=forms.Textarea(attrs={'rows': 12}),
        help_text='Separate paragraphs with a blank line. Sub-headings per unit are fine.',
    )
    skills_acquired = forms.CharField(
        required=False,
        label='Skills Acquired (Chapter 4)',
        widget=forms.Textarea(attrs={'rows': 10}),
        help_text='One skill per line — bullet points applied automatically.',
    )
    workshops = forms.CharField(
        required=False,
        label='Workshops (Chapter 5)',
        widget=forms.Textarea(attrs={'rows': 8}),
        help_text='Leave blank to use the workshop records from your logbook.',
    )
    lessons_learnt = forms.CharField(
        required=False,
        label='Lessons Learnt (Chapter 6)',
        widget=forms.Textarea(attrs={'rows': 10}),
        help_text='One lesson per line — bullet points applied automatically.',
    )
    observation = forms.CharField(
        required=False,
        label='Observations (Chapter 7)',
        widget=forms.Textarea(attrs={'rows': 10}),
        help_text='Separate paragraphs with a blank line.',
    )
    challenges = forms.CharField(
        required=False,
        label='Challenges (Chapter 8)',
        widget=forms.Textarea(attrs={'rows': 10}),
        help_text='Separate paragraphs with a blank line.',
    )
    recommendations = forms.CharField(
        required=False,
        label='Recommendations (Chapter 9)',
        widget=forms.Textarea(attrs={'rows': 10}),
        help_text='One recommendation per line — bullet points applied automatically.',
    )
    conclusion = forms.CharField(
        required=False,
        label='Conclusion (Chapter 10)',
        widget=forms.Textarea(attrs={'rows': 10}),
        help_text='Separate paragraphs with a blank line.',
    )


# ============================================================
# ✅ FIX v3 — Presentation custom-settings form
# Mirrors ReportOptionsForm structure: layout controls + section toggles.
# ============================================================

class PptxOptionsForm(StyledFormMixin, forms.Form):
    pptx_template = forms.ChoiceField(
        choices=[(k, v['name']) for k, v in PPTX_TEMPLATES.items()],
        label='Presentation template',
        help_text='Only COHY Classic shows the college logo — all other templates are logo-free designs.',
    )

    # ✅ PHASE 7: body font size for content-slide bullets.
    # Range expanded from 10–28 pt to 10–44 pt per the overflow-warning spec.
    # Sizes above ~28 pt often trigger the builder's "may have cut-off text"
    # banner — the user can still build, but is warned.
    font_size = forms.TypedChoiceField(
        choices=[
            (10, '10 pt'), (12, '12 pt'), (14, '14 pt'), (16, '16 pt'),
            (18, '18 pt (default)'), (20, '20 pt'), (22, '22 pt'),
            (24, '24 pt'), (26, '26 pt'), (28, '28 pt'),
            (30, '30 pt'), (32, '32 pt'), (34, '34 pt'), (36, '36 pt'),
            (38, '38 pt'), (40, '40 pt'), (42, '42 pt'), (44, '44 pt'),
        ],
        coerce=int, initial=18,
        label='Body font size (bullets on content slides)',
        help_text='Larger sizes fill the slide more; smaller sizes leave more breathing room. '
                  'Headings are unaffected. Sizes above 28 pt often overflow — you will see a '
                  'warning banner after building.',
    )

    # ✅ PHASE 7: two font family dropdowns.
    # Empty value = "Template default" (defer to the template's design).
    # Non-empty = hard override applied by PracticumPptxBuilder._apply_font_overrides().
    heading_font_family = forms.ChoiceField(
        required=False,
        choices=PPTX_FONT_CHOICES,
        initial='',
        label='Heading font',
        help_text='Font for slide titles. Leave on "Template default" to use the template’s built-in font.',
    )
    body_font_family = forms.ChoiceField(
        required=False,
        choices=PPTX_FONT_CHOICES,
        initial='',
        label='Body font',
        help_text='Font for bullet text. Leave on "Template default" to use the template’s built-in font.',
    )

    max_bullets = forms.TypedChoiceField(
        choices=[(3, '3 per slide'), (4, '4 per slide'), (5, '5 per slide'),
                 (6, '6 per slide'), (7, '7 per slide')],
        coerce=int, initial=6,
        label='Maximum bullets per slide',
        help_text='Fewer bullets = larger text and more white space.',
    )
    max_chars_per_bullet = forms.TypedChoiceField(
        choices=[(120, 'Short (120 characters)'),
                 (180, 'Medium (180 characters)'),
                 (220, 'Standard (220 characters)'),
                 (300, 'Long (300 characters)')],
        coerce=int, initial=220,
        label='Maximum characters per bullet',
    )
    include_speaker_notes = forms.BooleanField(
        required=False, initial=True,
        label='Include AI-generated speaker notes',
    )

    # Section toggles — map 1:1 to the slides the builder can emit
    slide_cover = forms.BooleanField(required=False, initial=True, label='Cover slide')
    slide_title = forms.BooleanField(required=False, initial=True,
                                     label='"Vacation Practicum Presentation" title slide')
    slide_intro = forms.BooleanField(required=False, initial=True, label='Introduction')
    slide_facility = forms.BooleanField(required=False, initial=True, label='Facility profile')
    slide_activities = forms.BooleanField(required=False, initial=True, label='Activities performed')
    slide_skills = forms.BooleanField(required=False, initial=True, label='Skills acquired')
    slide_challenges = forms.BooleanField(required=False, initial=True, label='Challenges faced')
    slide_observation = forms.BooleanField(required=False, initial=True, label='Observation & recommendation')
    slide_conclusion = forms.BooleanField(required=False, initial=True, label='Conclusion')
    slide_thankyou = forms.BooleanField(required=False, initial=True, label='"Thank You" slide')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in PPTX_SECTION_FIELDS:
            if name in self.fields:
                self.fields[name].widget.attrs['class'] = CHECKBOX_CLASSES


# ============================================================
# ✅ FIX v3 — Presentation content edit form (post-generation)
# One textarea per slide + a speaker-notes block.
# ============================================================

class PptxContentForm(StyledFormMixin, forms.Form):
    pptx_intro_bullets = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'rows': 6}),
        label='Introduction — one bullet per line',
    )
    pptx_facility_bullets = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'rows': 6}),
        label='Facility profile — one bullet per line',
    )
    pptx_activities_bullets = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'rows': 8}),
        label='Activities performed — one bullet per line',
    )
    pptx_skills_bullets = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'rows': 8}),
        label='Skills acquired — one bullet per line',
    )
    pptx_challenges_bullets = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'rows': 4}),
        label='Challenges faced — one bullet per line',
    )
    pptx_observation_recommendation_bullets = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'rows': 8}),
        label='Observation & recommendation — one bullet per line',
    )
    pptx_conclusion_bullets = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'rows': 6}),
        label='Conclusion — one bullet per line',
    )
    pptx_speaker_notes = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'rows': 8}),
        label='Speaker notes',
        help_text='One per slide, formatted as SLIDE HEADING: note text. '
                  'Leave a field blank to keep the AI draft for that slide.',
    )