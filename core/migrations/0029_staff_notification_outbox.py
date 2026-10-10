from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0028_whatsapp_bot")]
    operations = [
        migrations.AddField(model_name="access", name="email_daily_closing", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="access", name="email_weekly_review", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="access", name="email_monthly_review", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="access", name="email_critical_alerts", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="access", name="sms_daily_closing", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="access", name="sms_critical_alerts", field=models.BooleanField(default=False)),
        migrations.CreateModel(
            name="EmailNotice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_key", models.CharField(max_length=180, unique=True)),
                ("recipient_user", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("branch", models.ForeignKey(blank=True, null=True, on_delete=models.PROTECT, to="core.branch")),
                ("recipient", models.EmailField(max_length=254)),
                ("category", models.CharField(max_length=40)),
                ("subject", models.CharField(max_length=180)),
                ("body", models.TextField()),
                ("status", models.CharField(default="queued", max_length=16)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, max_length=240)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(model_name="emailnotice", index=models.Index(fields=["status", "created_at"], name="email_notice_queue_idx")),
    ]
