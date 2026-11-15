from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    DEPARTMENT_CHOICES = [
        ('HIM', 'Health Information Management'),
        ('PA', 'Physician Assistantship'),
        ('MED_LAB', 'Medical Laboratory Sciences'),
        ('DISEASE_CTRL', 'Disease Control & Epidemiology'),
        ('MENTAL_HEALTH', 'Community Mental Health'),
        ('NUTRITION', 'Community Health Nutrition'),
        ('HEALTH_PROM', 'Health Promotion'),
    ]

    # Override the default email field to enforce uniqueness
    email = models.EmailField(unique=True)
    
    index_number = models.CharField(max_length=20, unique=True)
    department = models.CharField(max_length=20, choices=DEPARTMENT_CHOICES)
    level = models.IntegerField(default=300)  # 100, 200, 300, 400
    phone_number = models.CharField(max_length=15, blank=True)

    # ===== Password reset via emailed code =====
    password_reset_code = models.CharField(max_length=6, blank=True)
    password_reset_expires_at = models.DateTimeField(null=True, blank=True)
    password_reset_attempts = models.IntegerField(default=0)

    # createsuperuser will ask for these too, so we never get blank duplicates
    REQUIRED_FIELDS = ['email', 'index_number', 'department']

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.index_number})"