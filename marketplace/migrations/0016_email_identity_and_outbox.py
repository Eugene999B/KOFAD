from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0015_hubtel_evidence")]

    operations = [
        migrations.CreateModel(
            name="EmailIdentity",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("staff", "Staff"), ("customer", "Customer")], max_length=12)),
                ("owner_id", models.PositiveBigIntegerField()),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("pending_email", models.EmailField(blank=True, max_length=254)),
                ("code_digest", models.CharField(blank=True, max_length=64)),
                ("requested_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("last_sent_at", models.DateTimeField(blank=True, null=True)),
                ("sends_window_start", models.DateTimeField(blank=True, null=True)),
                ("sends_in_window", models.PositiveSmallIntegerField(default=0)),
                ("code_attempts", models.PositiveSmallIntegerField(default=0)),
                ("notifications_enabled", models.BooleanField(default=False)),
            ],
        ),
        migrations.AddConstraint(
            model_name="emailidentity",
            constraint=models.UniqueConstraint(fields=("kind", "owner_id"), name="unique_kofad_email_identity"),
        ),
        migrations.AddConstraint(
            model_name="emailidentity",
            constraint=models.UniqueConstraint(condition=models.Q(verified_at__isnull=False), fields=("kind", "email"), name="unique_verified_email_per_account_type"),
        ),
        migrations.CreateModel(
            name="EmailNotice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_key", models.CharField(max_length=160, unique=True)),
                ("email", models.EmailField(max_length=254)),
                ("subject", models.CharField(max_length=200)),
                ("body", models.TextField()),
                ("status", models.CharField(choices=[("queued", "Queued"), ("sending", "Sending"), ("sent", "Sent"), ("failed", "Failed")], db_index=True, default="queued", max_length=12)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
