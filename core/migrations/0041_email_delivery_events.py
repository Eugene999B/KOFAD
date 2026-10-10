"""Store verified per-recipient delivery outcomes; provider accepted remains separate."""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("core", "0040_email_copied_recipients")]

    operations = [
        migrations.AddField(
            model_name="emailletter", name="delivery_status",
            field=models.CharField(
                max_length=20, default="unknown",
                choices=[
                    ("unknown", "Not confirmed"), ("requested", "Sent to provider"),
                    ("delivered", "Delivered"), ("deferred", "Deferred"),
                    ("soft_bounce", "Temporary bounce"), ("hard_bounce", "Hard bounce"),
                    ("blocked", "Blocked"), ("spam", "Spam complaint"),
                    ("invalid_email", "Invalid email"), ("error", "Delivery error"),
                    ("unsubscribed", "Unsubscribed"),
                ],
            ),
        ),
        migrations.AddField(
            model_name="emailletter", name="delivery_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="EmailDeliveryEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("fingerprint", models.CharField(max_length=64, unique=True)),
                ("provider_message_id", models.CharField(max_length=255)),
                ("recipient", models.EmailField(max_length=254)),
                ("event", models.CharField(max_length=32)),
                ("event_at", models.DateTimeField()),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("letter", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="delivery_events", to="core.emailletter"
                )),
            ],
            options={"ordering": ["-event_at", "-pk"]},
        ),
        migrations.AddIndex(
            model_name="emaildeliveryevent",
            index=models.Index(
                fields=["letter", "event_at"], name="kofad_mail_event_lookup"
            ),
        ),
    ]
