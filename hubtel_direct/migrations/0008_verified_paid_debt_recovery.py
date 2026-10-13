"""Safely distinguish Hubtel-provider-paid debt from unknown finance cases.

Historical finance_review rows are deliberately NOT backfilled automatically:
without independently stored paid evidence they require human verification.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("hubtel_direct", "0007_original_refund_order_lock")]

    operations = [
        migrations.AddField(
            model_name="debtdebitattempt",
            name="provider_verified_paid_at",
            field=models.DateTimeField(blank=True, null=True, db_index=True),
        ),
    ]
