import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class AttachmentSession(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='attachments')
    facility = models.ForeignKey('facilities.Facility', on_delete=models.SET_NULL, null=True)
    academic_year = models.CharField(max_length=20)  # e.g., "2025/2026"
    start_date = models.DateField()
    end_date = models.DateField()
    assigned_supervisor_name = models.CharField(max_length=250, blank=True)
    verification_token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    is_completed = models.BooleanField(default=False)

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'End date cannot be before the start date.'})

    def __str__(self):
        return f"{self.user} @ {self.facility} ({self.academic_year})"


class DailyLog(models.Model):
    attachment = models.ForeignKey(AttachmentSession, on_delete=models.CASCADE, related_name='daily_logs')
    date = models.DateField()
    week_number = models.IntegerField()  # Weeks 1 through 8
    unit_sub_department = models.CharField(max_length=100)  # e.g., Records Unit, OPD, Lab Section

    # Content
    activity_summary = models.TextField(help_text="Detailed description of tasks completed")
    tags = models.JSONField(default=list, blank=True)  # e.g., ["#ICD10", "#Registration"]

    # Quantitative
    numeric_tallies = models.JSONField(default=dict, blank=True)  # e.g., {"patients_registered": 45}

    # Department-specific text answers (e.g., chief complaints, EHR system used)
    department_details = models.JSONField(default=dict, blank=True)

    # Qualitative
    skills_acquired = models.TextField(blank=True)
    challenges_faced = models.TextField(blank=True)
    solutions_applied = models.TextField(blank=True)

    # Status
    created_offline = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date']
        unique_together = ['attachment', 'date']

    def save(self, *args, **kwargs):
        # Empty form/API input arrives as None; store sensible defaults instead
        if self.tags is None:
            self.tags = []
        if self.numeric_tallies is None:
            self.numeric_tallies = {}
        if self.department_details is None:
            self.department_details = {}
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.date} - {self.unit_sub_department}"