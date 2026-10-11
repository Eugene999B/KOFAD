from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0028_whatsapp_bot"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StaffInvitation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token_digest", models.CharField(max_length=64)),
                ("channel", models.CharField(max_length=12, choices=[("sms", "SMS"), ("email", "Email"), ("whatsapp", "WhatsApp")])),
                ("destination", models.CharField(max_length=254)),
                ("expires_at", models.DateTimeField()),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
                ("delivery_state", models.CharField(max_length=12, default="pending", choices=[("pending", "Pending"), ("submitted", "Submitted"), ("failed", "Failed")])),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="staff_invitation", to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
