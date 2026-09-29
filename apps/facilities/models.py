from django.db import models


class Facility(models.Model):
    name = models.CharField(max_length=255)  # e.g., Holy Family Hospital, Berekum
    region = models.CharField(max_length=100)
    district = models.CharField(max_length=100)
    facility_type = models.CharField(max_length=100)  # Teaching Hospital, District Hospital, Health Center
    history_and_background = models.TextField()
    vision_mission = models.TextField()
    organogram_description = models.TextField()
    is_verified = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Facilities"

    def __str__(self):
        return self.name