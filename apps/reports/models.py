# apps/reports/models.py

from django.db import models


class SchoolProfile(models.Model):
    """One record holding the school's name and logo, used on every report."""
    name = models.CharField(max_length=200, default='Yamfo College of Health (COHY)')
    logo = models.ImageField(
        upload_to='branding/', blank=True, null=True,
        help_text='PNG with a transparent background, at least 400 pixels wide, works best.'
    )
    motto = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = 'School profile'
        verbose_name_plural = 'School profile'

    def __str__(self):
        return self.name


class ReportFormatSample(models.Model):
    """Reference images of the official log book format, separated by department."""

    DEPARTMENT_CHOICES = [
        ('HIM', 'Health Information Management'),
        ('PA', 'Physician Assistant'),
        ('MED_LAB', 'Medical Laboratory'),
        ('DISEASE_CTRL', 'Disease Control'),
        ('MENTAL_HEALTH', 'Mental Health'),
        ('NUTRITION', 'Nutrition'),
        ('HEALTH_PROM', 'Health Promotion'),
    ]

    department = models.CharField(
        max_length=50,
        choices=DEPARTMENT_CHOICES,
        help_text='Which department uses this specific format?'
    )
    image = models.ImageField(
        upload_to='report_samples/',
        help_text='A photo or scan of one page from the log book / report guidelines.',
    )
    caption = models.CharField(
        max_length=200,
        blank=True,
        help_text='Optional label, e.g. "Chapter 2 weekly activity table".',
    )
    style_notes = models.TextField(
        blank=True,
        help_text=(
            "Plain-English guidance the AI uses when drafting sections in this "
            "format. e.g. 'Abstract must be 200-250 words, third person, mention "
            "facility and period, end with 4-6 keywords.' Leave blank to use the "
            "system default."
        ),
    )
    position = models.PositiveIntegerField(
        default=0,
        help_text='Order the samples should be shown in (lower numbers first).',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Report format sample'
        verbose_name_plural = 'Report format samples'
        ordering = ['department', 'position', 'uploaded_at']

    def __str__(self):
        return f"{self.department} - {self.caption or 'Sample'}"


class GeneratedReport(models.Model):
    attachment = models.OneToOneField(
        'logs.AttachmentSession',
        on_delete=models.CASCADE,
        related_name='report',
    )
    custom_logo = models.ImageField(upload_to='reports/logos/', blank=True, null=True)

    # ----- Report (PDF + Word) -----
    pdf_file  = models.FileField(upload_to='reports/pdf/',  blank=True, null=True)
    docx_file = models.FileField(upload_to='reports/docx/', blank=True, null=True)

    # ----- PowerPoint: .pptx and its PDF preview are always written together -----
    pptx_file     = models.FileField(upload_to='reports/pptx/',     blank=True, null=True)
    pptx_pdf_file = models.FileField(upload_to='reports/pptx_pdf/', blank=True, null=True)
    pptx_template = models.CharField(
        max_length=64,
        blank=True,
        default='cohy_classic',
        help_text='Key of the PowerPoint template used to build pptx_file.',
    )

    # ----- PowerPoint slide content (drafted lazily on first PPTX build) -----
    # Shape: {'pptx_intro_bullets': 'line1\nline2\n…', …, 'pptx_speaker_notes': '…'}
    pptx_content = models.JSONField(default=dict, blank=True)

    # ----- ✅ FIX v3: Custom presentation settings (mirrors report `options`) -----
    # Shape: {'max_bullets': 6, 'max_chars_per_bullet': 220,
    #         'include_speaker_notes': True,
    #         'sections': {'slide_cover': True, 'slide_intro': True, ...}}
    pptx_options = models.JSONField(default=dict, blank=True)

    options = models.JSONField(default=dict, blank=True)   # report format choices
    content = models.JSONField(default=dict, blank=True)   # AI drafts + student edits
    generated_at = models.DateTimeField(auto_now=True)
    is_signed_off = models.BooleanField(default=False)
    supervisor_signature_date = models.DateTimeField(null=True, blank=True)

    # ----- ✅ Regeneration tracking (shown on the admin All Users table) -----
    # Both counters increment only when the corresponding content field is
    # rewritten after it was already populated. First-time builds leave them at 0.
    regeneration_count = models.PositiveIntegerField(
        default=0,
        help_text='How many times this report was rebuilt after its first build.',
    )
    pptx_regeneration_count = models.PositiveIntegerField(
        default=0,
        help_text='How many times this presentation was rebuilt after its first build.',
    )

    def __str__(self):
        return f"Report for {self.attachment}"

    @property
    def signed_off(self):
        return self.is_signed_off

    @property
    def signed_date(self):
        return self.supervisor_signature_date