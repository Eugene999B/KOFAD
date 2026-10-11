from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0033_email_delivery_metrics")]
    operations = [
        migrations.AlterField("emailletter", "status",
            models.CharField(choices=[
                ("received", "Received"), ("queued", "Queued"),
                ("sending", "Sending"), ("submitted", "Submitted to provider"),
                ("internal", "Delivered internally"), ("failed", "Failed"),
                ("uncertain", "Needs review"),
                ("suppressed", "Suppressed by recipient preference"),
            ], max_length=12)),
    ]
