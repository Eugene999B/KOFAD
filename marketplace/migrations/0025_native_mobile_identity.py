"""Mobile PKCE grants and revocable device sessions; no pre-existing accounts affected."""
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("marketplace", "0024_email_notice_uncertain"),
        ("core", "0044_mobile_release_controls"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MobileAuthorizationGrant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code_hash", models.CharField(max_length=64, unique=True)),
                ("channel", models.CharField(choices=[("customer", "Customer"), ("staff", "Staff")], max_length=8)),
                ("customer_credential_stamp", models.CharField(blank=True, max_length=64)),
                ("staff_access_version", models.PositiveIntegerField(blank=True, null=True)),
                ("staff_mfa_at", models.FloatField(blank=True, null=True)),
                ("code_challenge", models.CharField(max_length=43)),
                ("redirect_uri", models.CharField(max_length=128)),
                ("client_id", models.CharField(max_length=40)),
                ("expires_at", models.DateTimeField()),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="core.branch")),
                ("customer", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="marketplace.customeraccount")),
                ("staff_user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"indexes": [models.Index(fields=["expires_at"], name="kof_mobile_grant_exp_idx")]},
        ),
        migrations.CreateModel(
            name="MobileDeviceSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("channel", models.CharField(choices=[("customer", "Customer"), ("staff", "Staff")], max_length=8)),
                ("customer_credential_stamp", models.CharField(blank=True, max_length=64)),
                ("staff_access_version", models.PositiveIntegerField(blank=True, null=True)),
                ("staff_mfa_at", models.FloatField(blank=True, null=True)),
                ("access_hash", models.CharField(max_length=64, unique=True)),
                ("refresh_hash", models.CharField(max_length=64, unique=True)),
                ("access_expires_at", models.DateTimeField()),
                ("refresh_expires_at", models.DateTimeField()),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_rotated_at", models.DateTimeField(blank=True, null=True)),
                ("branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="core.branch")),
                ("customer", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="marketplace.customeraccount")),
                ("staff_user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"indexes": [models.Index(fields=["refresh_expires_at"], name="kof_mobile_refresh_exp_idx")]},
        ),
    ]
