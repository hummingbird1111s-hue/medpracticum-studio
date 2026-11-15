import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class AttachmentSession(models.Model):
    SEMESTER_CHOICES = [
        ('FIRST SEMESTER', 'First Semester'),
        ('SECOND SEMESTER', 'Second Semester'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='attachments')
    facility = models.ForeignKey('facilities.Facility', on_delete=models.SET_NULL, null=True)
    academic_year = models.CharField(max_length=20)
    start_date = models.DateField()
    end_date = models.DateField()
    assigned_supervisor_name = models.CharField(max_length=250, blank=True)

    report_title = models.CharField(max_length=200, blank=True, help_text='e.g. "VACATION PRACTICUM I REPORT". Leave blank to let the system guess.')
    course_code = models.CharField(max_length=50, blank=True, help_text='e.g. "BIHM 115". Leave blank if unsure.')
    semester = models.CharField(max_length=30, choices=SEMESTER_CHOICES, blank=True, help_text='Which semester is this attachment for?')

    verification_token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    is_completed = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'End date cannot be before the start date.'})

    def __str__(self):
        return f"{self.user} @ {self.facility} ({self.academic_year})"


class DailyLog(models.Model):
    attachment = models.ForeignKey(AttachmentSession, on_delete=models.CASCADE, related_name='daily_logs')
    date = models.DateField()
    week_number = models.IntegerField()
    unit_sub_department = models.CharField(max_length=100)
    activity_summary = models.TextField(help_text="Detailed description of tasks completed")
    tags = models.JSONField(default=list, blank=True)
    numeric_tallies = models.JSONField(default=dict, blank=True)
    department_details = models.JSONField(default=dict, blank=True)
    skills_acquired = models.TextField(blank=True)
    challenges_faced = models.TextField(blank=True)
    solutions_applied = models.TextField(blank=True)
    created_offline = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ['date']

    def save(self, *args, **kwargs):
        if self.tags is None: self.tags = []
        if self.numeric_tallies is None: self.numeric_tallies = {}
        if self.department_details is None: self.department_details = {}
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.date} - {self.unit_sub_department}"


class Workshop(models.Model):
    TYPE_CHOICES = [
        ('Workshop', 'Workshop'),
        ('Meeting', 'Meeting'),
        ('Tutorial', 'Tutorial'),
    ]

    attachment = models.ForeignKey(AttachmentSession, on_delete=models.CASCADE, related_name='workshops')
    date = models.DateField()
    activity = models.CharField(max_length=255)
    type = models.CharField(max_length=50, choices=TYPE_CHOICES, default='Workshop')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date']

    def __str__(self):
        return f"{self.date} - {self.activity} ({self.type})"


# ==========================================
# AI CHAT — conversation-based
# ==========================================

class AIChatSession(models.Model):
    """
    One conversation. Scoped to a single user.
    `title` is auto-set from the first user message.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ai_chat_sessions',
    )
    title = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'AI chat session'
        verbose_name_plural = 'AI chat sessions'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['user', '-updated_at']),
        ]

    def __str__(self):
        return f"{self.user} — {self.title or 'Untitled'}"


class AIChatMessage(models.Model):
    """
    One message inside a conversation.
    `session` is nullable for backward compatibility with pre-conversation data.
    """
    ROLE_CHOICES = [
        ('user', 'User'),
        ('assistant', 'Assistant'),
    ]

    session = models.ForeignKey(
        AIChatSession,
        on_delete=models.CASCADE,
        related_name='messages',
        null=True, blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ai_chat_messages',
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'AI chat message'
        verbose_name_plural = 'AI chat messages'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['session', 'created_at']),
        ]

    def __str__(self):
        preview = (self.content[:50] + '…') if len(self.content) > 50 else self.content
        return f'{self.user} [{self.role}] {preview}'


# apps/logs/models.py  — append at the bottom

class AttachmentCompetency(models.Model):
    SOURCE_MANUAL = 'manual'
    SOURCE_OCR    = 'ocr'
    SOURCE_CHOICES = [
        (SOURCE_MANUAL, 'Manual'),
        (SOURCE_OCR,    'Uploaded / OCR'),
    ]

    attachment_session = models.ForeignKey(
        AttachmentSession,                       # ← the model that actually exists
        on_delete=models.CASCADE,
        related_name='competencies',
    )
    text   = models.CharField(max_length=500)
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    order  = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'id']
        indexes  = [models.Index(fields=['attachment_session', 'order'])]

    def __str__(self):
        return f"{self.attachment_session_id}: {self.text[:50]}"