import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import django.core.validators


def backfill_audit_event_ids(apps, schema_editor):
    Audit = apps.get_model("core", "Audit")
    for row in Audit.objects.filter(event_id__isnull=True).iterator():
        row.event_id = uuid.uuid4()
        row.save(update_fields=["event_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0019_creditors_accounts_payable"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="audit",
            name="event_id",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(backfill_audit_event_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="audit",
            name="event_id",
            field=models.UUIDField(default=uuid.uuid4, unique=True, editable=False),
        ),
        migrations.AddField(model_name="audit", name="category", field=models.CharField(blank=True, default="system", max_length=32)),
        migrations.AddField(model_name="audit", name="severity", field=models.CharField(blank=True, default="info", max_length=12)),
        migrations.AddField(model_name="audit", name="entity_type", field=models.CharField(blank=True, default="", max_length=60)),
        migrations.AddField(model_name="audit", name="entity_id", field=models.CharField(blank=True, default="", max_length=100)),
        migrations.AddField(model_name="audit", name="previous_hash", field=models.CharField(blank=True, default="", max_length=64)),
        migrations.AddField(model_name="audit", name="event_hash", field=models.CharField(blank=True, default="", max_length=64)),
        migrations.AddIndex(model_name="audit", index=models.Index(fields=["branch", "category", "created_at"], name="audit_category_time_idx")),
        migrations.AddIndex(model_name="audit", index=models.Index(fields=["branch", "severity", "created_at"], name="audit_severity_time_idx")),

        migrations.CreateModel(
            name="ReturnPrivilege",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("customer_returns", models.BooleanField(default=False)),
                ("supplier_returns", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("branch", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="core.branch")),
                ("granted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="return_privileges", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["user__username"]},
        ),
        migrations.AddConstraint(
            model_name="returnprivilege",
            constraint=models.UniqueConstraint(fields=("branch", "user"), name="one_return_privilege_per_user_branch"),
        ),

        migrations.CreateModel(
            name="CustomerReturnRequest",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("refund_method", models.CharField(choices=[("cash", "Cash"), ("momo", "MoMo"), ("bank", "Bank"), ("card", "Card")], default="cash", max_length=8)),
                ("reason", models.TextField()),
                ("status", models.CharField(choices=[("requested", "Waiting approval"), ("approved", "Approved"), ("rejected", "Rejected")], default="requested", max_length=12)),
                ("direct", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("branch", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="core.branch")),
                ("posted", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.document")),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("sale", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="customer_return_requests", to="core.document")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(model_name="customerreturnrequest", index=models.Index(fields=["branch", "status", "created_at"], name="customer_return_queue_idx")),

        migrations.CreateModel(
            name="CustomerReturnRequestLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1)])),
                ("disposition", models.CharField(choices=[("sellable", "Return to sellable stock"), ("quarantine", "Damaged / hold outside sellable stock")], default="sellable", max_length=12)),
                ("request", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="lines", to="core.customerreturnrequest")),
                ("source_line", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="customer_return_request_lines", to="core.line")),
            ],
            options={"ordering": ["pk"]},
        ),
        migrations.AddConstraint(model_name="customerreturnrequestline", constraint=models.UniqueConstraint(fields=("request", "source_line"), name="one_source_line_per_customer_return_request")),
        migrations.AddConstraint(model_name="customerreturnrequestline", constraint=models.CheckConstraint(condition=models.Q(("quantity__gt", 0)), name="customer_return_request_quantity_positive")),

        migrations.CreateModel(
            name="ManualJournal",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("journal_date", models.DateField()),
                ("reference", models.CharField(max_length=40)),
                ("memo", models.TextField()),
                ("status", models.CharField(choices=[("requested", "Waiting approval"), ("posted", "Posted"), ("rejected", "Rejected")], default="requested", max_length=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("branch", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="core.branch")),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-journal_date", "-created_at"]},
        ),
        migrations.AddConstraint(model_name="manualjournal", constraint=models.UniqueConstraint(fields=("branch", "reference"), name="unique_manual_journal_reference")),
        migrations.AddIndex(model_name="manualjournal", index=models.Index(fields=["branch", "status", "journal_date"], name="manual_journal_status_idx")),

        migrations.CreateModel(
            name="ManualJournalLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("account_code", models.CharField(max_length=20)),
                ("description", models.CharField(blank=True, max_length=200)),
                ("debit", models.DecimalField(decimal_places=2, default=0, max_digits=14, validators=[django.core.validators.MinValueValidator(0)])),
                ("credit", models.DecimalField(decimal_places=2, default=0, max_digits=14, validators=[django.core.validators.MinValueValidator(0)])),
                ("journal", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="lines", to="core.manualjournal")),
            ],
            options={"ordering": ["pk"]},
        ),
        migrations.AddConstraint(
            model_name="manualjournalline",
            constraint=models.CheckConstraint(
                condition=(models.Q(("credit", 0), ("debit__gt", 0)) | models.Q(("credit__gt", 0), ("debit", 0))),
                name="manual_journal_line_one_side",
            ),
        ),
    ]
