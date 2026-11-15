from django.contrib import admin
from .models import GeneratedReport, ReportFormatSample, SchoolProfile

@admin.register(SchoolProfile)
class SchoolProfileAdmin(admin.ModelAdmin):
    list_display = ('name', 'motto')

@admin.register(ReportFormatSample)
class ReportFormatSampleAdmin(admin.ModelAdmin):
    # Changed 'school' to 'department'
    list_display = ('__str__', 'department', 'position', 'uploaded_at')
    list_filter = ('department',)
    list_editable = ('position',)
    ordering = ('department', 'position', 'uploaded_at')
    fields = ('department', 'image', 'caption', 'style_notes', 'position')

@admin.register(GeneratedReport)
class GeneratedReportAdmin(admin.ModelAdmin):
    list_display = ('attachment', 'generated_at', 'is_signed_off')
    list_filter = ('is_signed_off',)
    readonly_fields = ('generated_at',)