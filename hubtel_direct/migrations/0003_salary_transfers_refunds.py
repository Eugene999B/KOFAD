import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("hubtel_direct", "0002_debtmandate_debtdebitattempt"),
        ("core", "0048_telegram_optional_phone_confirmation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(name="WorkerTransferProfile", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("payment_type", models.CharField(max_length=10, choices=[("momo", "Mobile Money"), ("bank", "Bank")])),
            ("channel", models.CharField(max_length=20, blank=True)),
            ("bank_code", models.CharField(max_length=6, blank=True)),
            ("destination_encrypted", models.TextField()),
            ("destination_suffix", models.CharField(max_length=4)),
            ("provider_name", models.CharField(max_length=140)),
            ("verified_at", models.DateTimeField()),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("worker", models.OneToOneField(to="core.worker", related_name="hubtel_salary_destination", on_delete=django.db.models.deletion.PROTECT)),
            ("verified_by", models.ForeignKey(to=settings.AUTH_USER_MODEL, related_name="+", on_delete=django.db.models.deletion.PROTECT)),
        ]),
        migrations.CreateModel(name="PayrollTransferBatch", fields=[
            ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ("status", models.CharField(max_length=24, default="draft", db_index=True)),
            ("expected_amount", models.DecimalField(max_digits=14, decimal_places=2)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("approved_at", models.DateTimeField(null=True, blank=True)),
            ("period", models.OneToOneField(to="core.payrollperiod", related_name="hubtel_transfer_batch", on_delete=django.db.models.deletion.PROTECT)),
            ("created_by", models.ForeignKey(to=settings.AUTH_USER_MODEL, related_name="+", on_delete=django.db.models.deletion.PROTECT)),
            ("approved_by", models.ForeignKey(to=settings.AUTH_USER_MODEL, related_name="+", null=True, blank=True, on_delete=django.db.models.deletion.PROTECT)),
        ]),
        migrations.CreateModel(name="PayrollTransferLine", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("request_key", models.UUIDField(default=uuid.uuid4, unique=True, editable=False)),
            ("amount", models.DecimalField(max_digits=14, decimal_places=2)),
            ("encrypted_account_snapshot", models.TextField()),
            ("recipient_name_snapshot", models.CharField(max_length=140)),
            ("channel_snapshot", models.CharField(max_length=20, blank=True)),
            ("bank_code_snapshot", models.CharField(max_length=6, blank=True)),
            ("status", models.CharField(max_length=24, default="draft", db_index=True)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("batch", models.ForeignKey(to="hubtel_direct.payrolltransferbatch", related_name="lines", on_delete=django.db.models.deletion.PROTECT)),
            ("entry", models.OneToOneField(to="core.payrollentry", related_name="hubtel_transfer_line", on_delete=django.db.models.deletion.PROTECT)),
            ("destination", models.ForeignKey(to="hubtel_direct.workertransferprofile", on_delete=django.db.models.deletion.PROTECT)),
            ("transaction", models.OneToOneField(to="hubtel_direct.directtransaction", null=True, blank=True, on_delete=django.db.models.deletion.PROTECT)),
            ("payroll_payment", models.OneToOneField(to="core.payrollpayment", null=True, blank=True, on_delete=django.db.models.deletion.PROTECT)),
        ], options={"indexes": [models.Index(fields=["batch", "status"], name="hubtel_pay_line_status_idx")]}),
        migrations.CreateModel(name="HubtelRefundCase", fields=[
            ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ("original_hubtel_order_id", models.CharField(max_length=64, unique=True)),
            ("original_transaction_reference", models.CharField(max_length=100)),
            ("amount", models.DecimalField(max_digits=14, decimal_places=2)),
            ("status", models.CharField(max_length=24, default="review_pending", db_index=True)),
            ("submitted_at", models.DateTimeField(null=True, blank=True)),
            ("confirmed_at", models.DateTimeField(null=True, blank=True)),
            ("last_provider_code", models.CharField(max_length=24, blank=True)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("return_request", models.OneToOneField(to="marketplace.marketreturnrequest", related_name="hubtel_refund_case", on_delete=django.db.models.deletion.PROTECT)),
            ("created_by", models.ForeignKey(to=settings.AUTH_USER_MODEL, related_name="+", on_delete=django.db.models.deletion.PROTECT)),
            ("approved_by", models.ForeignKey(to=settings.AUTH_USER_MODEL, related_name="+", null=True, blank=True, on_delete=django.db.models.deletion.PROTECT)),
        ]),
    ]
