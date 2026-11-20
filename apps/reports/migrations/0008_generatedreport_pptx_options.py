from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reports', '0007_generatedreport_pptx_content_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='generatedreport',
            name='pptx_options',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]