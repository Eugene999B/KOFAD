"""Quarantine ambiguous transactional sends instead of automatically resending."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("marketplace", "0023_online_payment_all_in_pricing"),
    ]

    operations = [
        migrations.AlterField(
            model_name="emailnotice",
            name="status",
            field=models.CharField(
                max_length=12, default="queued", db_index=True,
                choices=[
                    ("queued", "Queued"), ("sending", "Sending"),
                    ("sent", "Sent"), ("failed", "Failed"),
                    ("uncertain", "Needs review"),
                ],
            ),
        ),
    ]
