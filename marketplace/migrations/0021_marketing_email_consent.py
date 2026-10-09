from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0020_customer_email_recovery")]
    operations = [
        migrations.AddField("emailidentity", "marketing_emails_enabled",
                            models.BooleanField(default=False)),
    ]
