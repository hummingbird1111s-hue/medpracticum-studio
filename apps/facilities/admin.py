from django.contrib import admin
from .models import Facility

@admin.register(Facility)
class FacilityAdmin(admin.ModelAdmin):
    list_display = ('name', 'facility_type', 'region', 'district', 'is_verified')
    list_filter = ('is_verified', 'region', 'facility_type')
    search_fields = ('name', 'region', 'district')