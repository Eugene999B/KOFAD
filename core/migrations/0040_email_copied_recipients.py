"""Optional copied recipients for staff-created mail; no existing records changed."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0039_professional_email_workflow")]

    operations = [
        migrations.AddField(
            model_name="emailletter", name="cc_addresses",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="emailletter", name="bcc_addresses",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="emailstaffdraft", name="cc_addresses",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="emailstaffdraft", name="bcc_addresses",
            field=models.TextField(blank=True),
        ),
    ]
