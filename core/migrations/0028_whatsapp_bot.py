from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0027_channel_automation_controls"),
        ("marketplace", "0012_receiving_account_details"),
    ]
    operations = [
        migrations.CreateModel(
            name="WhatsAppBotContact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("wa_id", models.CharField(max_length=40)),
                ("phone_number_id", models.CharField(max_length=80)),
                ("opted_out", models.BooleanField(default=False)),
                ("handoff", models.BooleanField(default=False)),
                ("last_inbound_at", models.DateTimeField(null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("conversation", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="marketplace.conversation")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("wa_id", "phone_number_id"), name="wa_bot_contact_unique")]},
        ),
        migrations.CreateModel(
            name="WhatsAppBotReply",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_key", models.CharField(max_length=220, unique=True)),
                ("body", models.TextField()),
                ("status", models.CharField(db_index=True, default="queued", max_length=20)),
                ("provider_id", models.CharField(blank=True, db_index=True, max_length=180)),
                ("error", models.CharField(blank=True, max_length=240)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("contact", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="core.whatsappbotcontact")),
            ],
        ),
    ]
