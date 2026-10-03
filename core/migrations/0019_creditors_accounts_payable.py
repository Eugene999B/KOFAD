from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0018_workforce_payroll_accounting"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="document_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="document",
            name="external_reference",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="document",
            name="payable_category",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AlterField(
            model_name="document",
            name="kind",
            field=models.CharField(
                choices=[
                    ("sale", "Sale"),
                    ("purchase", "Purchase"),
                    ("creditor_charge", "Creditor bill"),
                    ("return", "Return"),
                    ("supplier_return", "Supplier return"),
                    ("inventory_writeoff", "Inventory write-off"),
                    ("expense", "Expense"),
                    ("collection", "Debt payment"),
                    ("supplier_payment", "Supplier payment"),
                    ("reversal", "Reversal"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddIndex(
            model_name="document",
            index=models.Index(fields=["branch", "party", "external_reference"], name="supplier_invoice_ref_idx"),
        ),
    ]
