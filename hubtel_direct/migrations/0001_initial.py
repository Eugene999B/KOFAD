import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("marketplace", "0026_merge_customer_email"),
    ]
    operations = [
        migrations.CreateModel(
            name="DirectTransaction",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("client_reference", models.CharField(max_length=36, unique=True, editable=False)),
                ("request_key", models.UUIDField(null=True, blank=True, unique=True, editable=False)),
                ("request_fingerprint", models.CharField(max_length=64, blank=True, editable=False)),
                ("kind", models.CharField(max_length=16, choices=[("receive", "Receive MoMo"), ("send_momo", "Send MoMo"), ("send_bank", "Send to bank")])),
                ("amount", models.DecimalField(max_digits=14, decimal_places=2)),
                ("channel", models.CharField(max_length=20, blank=True)),
                ("bank_code", models.CharField(max_length=6, blank=True)),
                ("destination_encrypted", models.TextField()),
                ("destination_suffix", models.CharField(max_length=4, blank=True)),
                ("recipient_name", models.CharField(max_length=140, blank=True)),
                ("status", models.CharField(max_length=24, default="approval_pending", db_index=True)),
                ("hubtel_transaction_id", models.CharField(max_length=100, blank=True)),
                ("provider_code", models.CharField(max_length=20, blank=True)),
                ("callback_seen_at", models.DateTimeField(null=True, blank=True)),
                ("next_check_at", models.DateTimeField(null=True, blank=True, db_index=True)),
                ("check_count", models.PositiveIntegerField(default=0)),
                ("checked_at", models.DateTimeField(null=True, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("approved_by", models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.PROTECT, related_name="hubtel_direct_approvals", to=settings.AUTH_USER_MODEL)),
                ("requested_by", models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.PROTECT, related_name="hubtel_direct_requests", to=settings.AUTH_USER_MODEL)),
                ("customer", models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.PROTECT, to="marketplace.customeraccount")),
                ("order", models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.PROTECT, to="marketplace.onlineorder")),
            ],
            options={"indexes":[
                models.Index(fields=["status", "next_check_at"], name="hubtel_direct_due_idx"),
                models.Index(fields=["order", "-created_at"], name="hubtel_direct_order_idx"),
            ]},
        ),
    ]
