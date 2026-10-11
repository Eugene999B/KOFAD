from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0034_email_suppressed_status")]
    operations = [
        migrations.AddField("emailcampaign", "completed_queuing_at",
                            models.DateTimeField(blank=True, null=True)),
    ]
