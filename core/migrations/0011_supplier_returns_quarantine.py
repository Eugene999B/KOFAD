import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("core", "0010_business_policy_controls")]

    operations = [
        migrations.AlterField(
            model_name="document",
            name="kind",
            field=models.CharField(
                choices=[
                    ("sale", "Sale"), ("purchase", "Purchase"), ("return", "Return"),
                    ("supplier_return", "Supplier return"), ("inventory_writeoff", "Inventory write-off"), ("expense", "Expense"),
                    ("collection", "Debt payment"), ("supplier_payment", "Supplier payment"),
                    ("reversal", "Reversal"),
                ],
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="SupplierReturn",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("quantity", models.PositiveIntegerField(validators=[MinValueValidator(1)])),
                ("refund_method", models.CharField(
                    choices=[("cash", "Cash"), ("momo", "MoMo"), ("bank", "Bank"), ("card", "Card")],
                    default="cash", max_length=8)),
                ("reason", models.TextField()),
                ("status", models.CharField(
                    choices=[("requested", "Requested"), ("approved", "Approved"), ("rejected", "Rejected")],
                    default="requested", max_length=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("branch", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="core.branch")),
                ("posted", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to="core.document")),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to=settings.AUTH_USER_MODEL)),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to=settings.AUTH_USER_MODEL)),
                ("source_line", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="supplier_returns", to="core.line")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="QuarantineItem",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("quantity", models.PositiveIntegerField(validators=[MinValueValidator(1)])),
                ("unit_cost", models.DecimalField(decimal_places=2, max_digits=14, validators=[MinValueValidator(0)])),
                ("reason", models.TextField()),
                ("status", models.CharField(
                    choices=[
                        ("requested", "Requested"), ("held", "Held in quarantine"), ("rejected", "Rejected"),
                        ("released", "Released to sellable stock"), ("written_off", "Written off"),
                    ],
                    default="requested", max_length=16)),
                ("resolution_note", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("branch", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="core.branch")),
                ("loss_document", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to="core.document")),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="core.product")),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to=settings.AUTH_USER_MODEL)),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to=settings.AUTH_USER_MODEL)),
                ("resolved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddField(
            model_name="transferreceipt",
            name="loss_document",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="+", to="core.document"),
        ),
        migrations.AddConstraint(
            model_name="supplierreturn",
            constraint=models.CheckConstraint(condition=models.Q(quantity__gt=0),
                name="supplier_return_positive_quantity"),
        ),
        migrations.AddConstraint(
            model_name="quarantineitem",
            constraint=models.CheckConstraint(condition=models.Q(quantity__gt=0),
                name="quarantine_positive_quantity"),
        ),
        migrations.AddConstraint(
            model_name="quarantineitem",
            constraint=models.CheckConstraint(condition=models.Q(unit_cost__gte=0),
                name="quarantine_nonnegative_cost"),
        ),
    ]
