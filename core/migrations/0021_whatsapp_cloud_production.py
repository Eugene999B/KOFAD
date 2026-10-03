import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0020_control_centre_intelligence_accounting"),
    ]

    operations = [
        migrations.CreateModel(
            name="WhatsAppAttempt",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("number", models.PositiveIntegerField()),
                ("provider_id", models.CharField(blank=True, db_index=True, max_length=180)),
                ("status", models.CharField(default="sending", max_length=20)),
                ("template_name", models.CharField(blank=True, max_length=120)),
                ("media_id", models.CharField(blank=True, max_length=180)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("http_status", models.PositiveIntegerField(blank=True, null=True)),
                ("error_code", models.CharField(blank=True, max_length=80)),
                ("error_detail", models.CharField(blank=True, max_length=240)),
                ("message", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="whatsapp_attempts", to="core.message")),
            ],
            options={"ordering": ["-started_at"]},
        ),
        migrations.AddConstraint(
            model_name="whatsappattempt",
            constraint=models.UniqueConstraint(fields=("message", "number"), name="unique_whatsapp_attempt"),
        ),
        migrations.CreateModel(
            name="WhatsAppWebhookEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("fingerprint", models.CharField(max_length=64, unique=True)),
                ("waba_id", models.CharField(blank=True, max_length=80)),
                ("phone_number_id", models.CharField(blank=True, max_length=80)),
                ("event_type", models.CharField(max_length=24)),
                ("provider_message_id", models.CharField(blank=True, db_index=True, max_length=180)),
                ("wa_id", models.CharField(blank=True, db_index=True, max_length=40)),
                ("status", models.CharField(blank=True, max_length=20)),
                ("payload", models.JSONField(default=dict)),
                ("received_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-received_at"]},
        ),
        migrations.AddIndex(
            model_name="whatsappwebhookevent",
            index=models.Index(fields=["event_type", "received_at"], name="wa_event_type_time_idx"),
        ),
        migrations.AddIndex(
            model_name="whatsappwebhookevent",
            index=models.Index(fields=["status", "received_at"], name="wa_status_time_idx"),
        ),
    ]
