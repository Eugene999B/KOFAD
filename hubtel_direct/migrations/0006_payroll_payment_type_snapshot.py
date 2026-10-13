"""Freeze each salary transfer's verified payment rail.

Any batches prepared against a previous app version are backfilled from their
original verified recipient profile. The sender will never use a subsequently
edited live profile to decide MoMo versus bank.
"""
from django.db import migrations, models


def backfill_existing_lines(apps, schema_editor):
    Line = apps.get_model("hubtel_direct", "PayrollTransferLine")
    for line in Line.objects.select_related("destination").iterator(chunk_size=500):
        payment_type = line.destination.payment_type
        if payment_type in {"momo", "bank"} and line.payment_type_snapshot != payment_type:
            Line.objects.filter(pk=line.pk).update(payment_type_snapshot=payment_type)


class Migration(migrations.Migration):
    dependencies = [("hubtel_direct", "0005_hubtel_refund_review")]

    operations = [
        migrations.AddField(
            model_name="payrolltransferline",
            name="payment_type_snapshot",
            field=models.CharField(
                choices=[("momo", "Mobile Money"), ("bank", "Bank")],
                max_length=10, default="momo",
            ),
        ),
        migrations.RunPython(backfill_existing_lines, migrations.RunPython.noop),
    ]
