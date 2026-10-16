"""Persist MoMo debt requests before the first network send."""
import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("hubtel_direct", "0012_invoice_drafts_and_pending_timeout"),
        ("core", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DebtMomoCollection",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("request_key", models.UUIDField(unique=True, editable=False)),
                ("amount", models.DecimalField(max_digits=14, decimal_places=2)),
                ("channel", models.CharField(max_length=20)),
                ("phone_encrypted", models.TextField(editable=False)),
                ("phone_suffix", models.CharField(max_length=4)),
                ("wallet_name", models.CharField(max_length=140)),
                ("reference", models.CharField(max_length=36, unique=True)),
                ("provider_transaction_id", models.CharField(max_length=100, blank=True)),
                ("provider_code", models.CharField(max_length=24, blank=True)),
                ("status", models.CharField(max_length=24, default="initializing", db_index=True)),
                ("checked_at", models.DateTimeField(blank=True, null=True)),
                ("next_check_at", models.DateTimeField(blank=True, null=True, db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("branch", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="core.branch")),
                ("party", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="core.party")),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ("receipt", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="core.document")),
            ],
        ),
        migrations.AddIndex(
            model_name="debtmomocollection",
            index=models.Index(fields=["status", "next_check_at"], name="hubtel_debt_momo_due_idx"),
        ),
    ]
