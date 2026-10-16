"""Store last independently observed Hubtel Direct transaction status.

Never equate payment acceptance (0001) with a settled PAID transaction.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("hubtel_direct", "0013_debt_momo_collection")]

    operations = [
        migrations.AddField(
            model_name="directtransaction",
            name="provider_status",
            field=models.CharField(max_length=32, blank=True),
        ),
    ]
