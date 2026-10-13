import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("hubtel_direct", "0001_initial"),
        ("core", "0048_telegram_optional_phone_confirmation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="DebtMandate",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("phone_encrypted", models.TextField()),
                ("phone_fingerprint", models.CharField(max_length=64, db_index=True)),
                ("phone_suffix", models.CharField(max_length=4)),
                ("channel", models.CharField(max_length=28)),
                ("client_reference", models.CharField(max_length=36, unique=True, editable=False)),
                ("hubtel_preapproval_id", models.CharField(max_length=100, blank=True)),
                ("verification_type", models.CharField(max_length=8, blank=True)),
                ("otp_prefix", models.CharField(max_length=8, blank=True)),
                ("status", models.CharField(max_length=18, choices=[
                    ("draft", "Awaiting setup"), ("pending", "Awaiting customer authorization"),
                    ("approved", "Approved by wallet holder"), ("cancel_pending", "Cancellation processing"),
                    ("cancelled", "Cancelled"), ("suspended", "Suspended"),
                    ("settled", "Settled"), ("failed", "Authorization failed"),
                ], default="draft", db_index=True)),
                ("frequency", models.CharField(max_length=12, choices=[
                    ("once", "One charge when overdue"), ("daily", "Daily"),
                    ("weekly", "Weekly"), ("monthly", "Monthly"),
                    ("quarterly", "Quarterly"), ("yearly", "Yearly"),
                ])),
                ("first_due_date", models.DateField()),
                ("next_due_date", models.DateField()),
                ("amount_per_period", models.DecimalField(max_digits=14, decimal_places=2)),
                ("maximum_total", models.DecimalField(max_digits=14, decimal_places=2)),
                ("collected_total", models.DecimalField(max_digits=14, decimal_places=2, default=0)),
                ("lower_partial_payment", models.BooleanField(default=False)),
                ("minimum_partial", models.DecimalField(max_digits=14, decimal_places=2, default=0)),
                ("maximum_attempts_per_period", models.PositiveSmallIntegerField(default=1)),
                ("terms_snapshot", models.JSONField(default=dict)),
                ("provider_approved_at", models.DateTimeField(null=True, blank=True)),
                ("cancellation_requested_at", models.DateTimeField(null=True, blank=True)),
                ("cancelled_at", models.DateTimeField(null=True, blank=True)),
                ("last_provider_check_at", models.DateTimeField(null=True, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("branch", models.ForeignKey(to="core.branch", related_name="hubtel_debt_mandates", on_delete=django.db.models.deletion.PROTECT)),
                ("party", models.ForeignKey(to="core.party", related_name="hubtel_debt_mandates", on_delete=django.db.models.deletion.PROTECT)),
                ("created_by", models.ForeignKey(to=settings.AUTH_USER_MODEL, related_name="hubtel_debt_mandates_created", on_delete=django.db.models.deletion.PROTECT)),
            ],
            options={"indexes": [
                models.Index(fields=["branch", "status", "next_due_date"], name="hubtel_debt_due_idx"),
                models.Index(fields=["party", "-created_at"], name="hubtel_debt_party_idx"),
            ], "constraints": [models.UniqueConstraint(fields=["phone_fingerprint"], condition=models.Q(status__in=["draft", "pending", "approved", "cancel_pending"]), name="hubtel_one_active_mandate_wallet")]},
        ),
        migrations.CreateModel(
            name="DebtDebitAttempt",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("reference", models.CharField(max_length=36, unique=True, editable=False)),
                ("period_due_date", models.DateField()),
                ("amount", models.DecimalField(max_digits=14, decimal_places=2)),
                ("status", models.CharField(max_length=24, default="planned", db_index=True)),
                ("hubtel_transaction_id", models.CharField(max_length=100, blank=True)),
                ("provider_code", models.CharField(max_length=24, blank=True)),
                ("next_check_at", models.DateTimeField(null=True, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("mandate", models.ForeignKey(to="hubtel_direct.debtmandate", related_name="attempts", on_delete=django.db.models.deletion.PROTECT)),
                ("collection_document", models.OneToOneField(to="core.document", null=True, blank=True, on_delete=django.db.models.deletion.PROTECT)),
            ],
            options={"indexes": [
                models.Index(fields=["status", "next_check_at"], name="hubtel_debit_attempt_due"),
            ]},
        ),
    ]
