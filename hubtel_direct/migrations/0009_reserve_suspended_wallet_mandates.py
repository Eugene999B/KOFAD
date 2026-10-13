"""Retain wallet exclusivity while an old mandate is suspended.

Suspended is NOT the same as cancelled: the wallet may still have approved
provider authority or unsettled debit attempts. A new mandate must not be
created until the old one is explicitly cancelled and reconciled.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("hubtel_direct", "0008_verified_paid_debt_recovery")]

    operations = [
        migrations.RemoveConstraint(
            model_name="debtmandate",
            name="hubtel_one_active_mandate_wallet",
        ),
        migrations.AddConstraint(
            model_name="debtmandate",
            constraint=models.UniqueConstraint(
                fields=["phone_fingerprint"],
                condition=models.Q(
                    status__in=[
                        "draft", "pending", "approved", "cancel_pending",
                        "suspended",
                    ]
                ),
                name="hubtel_one_active_mandate_wallet",
            ),
        ),
    ]
