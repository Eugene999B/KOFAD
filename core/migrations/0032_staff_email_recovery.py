from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0031_customer_service_contacts")]
    operations = [
        migrations.AddField("passwordrecovery", "email", models.EmailField(blank=True, max_length=254)),
        migrations.AddField("passwordrecovery", "channel",
            models.CharField(choices=[("sms", "SMS"), ("email", "Email")], default="sms", max_length=10)),
    ]
