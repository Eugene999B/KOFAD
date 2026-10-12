"""Add optional phone confirmation to Telegram pairing without changing live accounts."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0047_telegram_business_integration")]

    operations = [
        migrations.AddField(
            model_name="telegramlinkchallenge",
            name="proposed_phone",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="telegramlinkchallenge",
            name="phone_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
