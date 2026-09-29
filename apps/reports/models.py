from django.db import models


class GeneratedReport(models.Model):
    attachment = models.OneToOneField('logs.AttachmentSession', on_delete=models.CASCADE, related_name='report')
    pdf_file = models.FileField(upload_to='reports/pdf/', blank=True, null=True)
    docx_file = models.FileField(upload_to='reports/docx/', blank=True, null=True)
    pptx_file = models.FileField(upload_to='reports/pptx/', blank=True, null=True)
    generated_at = models.DateTimeField(auto_now=True)
    is_signed_off = models.BooleanField(default=False)
    supervisor_signature_date = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Report for {self.attachment}"