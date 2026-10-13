"""Enforce at most one Hubtel refund review record per original online order."""
import django.db.models.deletion
from django.db import migrations, models


def link_existing_reviews(apps, schema_editor):
    Case = apps.get_model("hubtel_direct", "HubtelRefundCase")
    used = set()
    for case in Case.objects.select_related("return_request").order_by("created_at", "pk").iterator():
        online_order_id = case.return_request.order_id
        if online_order_id in used:
            raise RuntimeError(
                "Two Hubtel refund cases reference the same online order. "
                "Resolve duplicate historical cases before applying this migration."
            )
        used.add(online_order_id)
        Case.objects.filter(pk=case.pk).update(original_order_id=online_order_id)


class Migration(migrations.Migration):
    dependencies = [("hubtel_direct", "0006_payroll_payment_type_snapshot")]

    operations = [
        migrations.AddField(
            model_name="hubtelrefundcase",
            name="original_order",
            field=models.OneToOneField(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="hubtel_original_refund_case", to="marketplace.onlineorder",
            ),
        ),
        migrations.RunPython(link_existing_reviews, migrations.RunPython.noop),
    ]
