"""Allow multiple unapproved invoice drafts while reserving one provider authority per wallet.

Ten-minute local timeout does not grant rights to retry until Hubtel cancellation
has been independently confirmed.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("hubtel_direct", "0011_directdebitsaleintent")]

    operations = [
        migrations.AlterField(
            model_name="debtmandate", name="status",
            field=models.CharField(
                max_length=18, db_index=True, default="draft",
                choices=[
                    ("draft", "Awaiting setup"),
                    ("pending", "Awaiting customer authorization"),
                    ("approved", "Approved by wallet holder"),
                    ("cancel_pending", "Cancellation processing"),
                    ("cancelled", "Cancelled"),
                    ("suspended", "Suspended — not active"),
                    ("expired_review", "Unsuccessful — approval timed out; clear provider request"),
                    ("settled", "Settled"),
                    ("failed", "Authorization failed"),
                ],
            ),
        ),
        migrations.RemoveConstraint(
            model_name="debtmandate", name="hubtel_one_active_mandate_wallet",
        ),
        migrations.AddConstraint(
            model_name="debtmandate",
            constraint=models.UniqueConstraint(
                fields=["phone_fingerprint"],
                condition=models.Q(status__in=[
                    "pending", "approved", "cancel_pending", "suspended", "expired_review",
                ]),
                name="hubtel_one_active_mandate_wallet",
            ),
        ),
    ]
