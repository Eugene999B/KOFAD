"""Durable authorization-before-sale checkout; no legacy invoice mutation."""
import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("hubtel_direct", "0010_debt_setup_ready_by_default"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DirectDebitSaleIntent",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("request_key", models.UUIDField(unique=True)),
                ("request_fingerprint", models.CharField(max_length=64)),
                ("checkout_payload", models.JSONField(default=dict)),
                ("terms_snapshot", models.JSONField(default=dict)),
                ("phone_encrypted", models.TextField()),
                ("phone_fingerprint", models.CharField(max_length=64, db_index=True)),
                ("phone_suffix", models.CharField(max_length=4)),
                ("channel", models.CharField(max_length=28)),
                ("frequency", models.CharField(max_length=12)),
                ("first_due_date", models.DateField()),
                ("final_due_date", models.DateField()),
                ("quoted_total", models.DecimalField(max_digits=14, decimal_places=2)),
                ("quoted_paid", models.DecimalField(max_digits=14, decimal_places=2)),
                ("outstanding", models.DecimalField(max_digits=14, decimal_places=2)),
                ("client_reference", models.CharField(max_length=36, unique=True)),
                ("hubtel_preapproval_id", models.CharField(max_length=100, blank=True)),
                ("verification_type", models.CharField(max_length=8, blank=True)),
                ("otp_prefix", models.CharField(max_length=8, blank=True)),
                ("status", models.CharField(max_length=20, default="draft", db_index=True)),
                ("provider_approved_at", models.DateTimeField(null=True, blank=True)),
                ("last_provider_check_at", models.DateTimeField(null=True, blank=True)),
                ("next_provider_check_at", models.DateTimeField(null=True, blank=True, db_index=True)),
                ("request_sent_at", models.DateTimeField(null=True, blank=True)),
                ("callback_seen_at", models.DateTimeField(null=True, blank=True)),
                ("last_error", models.CharField(max_length=240, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("branch", models.ForeignKey(to="core.branch", on_delete=django.db.models.deletion.PROTECT, related_name="direct_debit_sale_intents")),
                ("created_by", models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.PROTECT, related_name="direct_debit_sale_intents")),
                ("party", models.ForeignKey(to="core.party", null=True, blank=True, on_delete=django.db.models.deletion.PROTECT)),
                ("document", models.OneToOneField(to="core.document", null=True, blank=True, on_delete=django.db.models.deletion.PROTECT)),
                ("mandate", models.OneToOneField(to="hubtel_direct.debtmandate", null=True, blank=True, on_delete=django.db.models.deletion.PROTECT)),
            ],
            options={"indexes": [
                models.Index(fields=["status", "next_provider_check_at"], name="hubtel_v2_intent_due_idx"),
                models.Index(fields=["branch", "-created_at"], name="hubtel_v2_intent_branch_idx"),
            ]},
        ),
    ]
