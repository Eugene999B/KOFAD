"""Make debt agreement preparation usable while all money-movement gates stay OFF.

This is an explicit one-time rollout choice: enable staff drafting on existing
branch policies as requested by management. It does NOT change
pause_auto_collection or any Hubtel provider/Railway enablement flag.
"""
from django.db import migrations, models


def enable_agreement_drafting(apps, schema_editor):
    Policy = apps.get_model("hubtel_direct", "OnlineDebtSettings")
    Policy.objects.filter(plan_creation_enabled=False).update(plan_creation_enabled=True)
    Policy.objects.filter(allow_daily=False).update(allow_daily=True)
    Policy.objects.filter(allow_yearly=False).update(allow_yearly=True)
    # Only update branches still using the previous default thresholds.
    Policy.objects.filter(min_open_balance="10.00").update(min_open_balance="1.00")
    Policy.objects.filter(min_installment="5.00").update(min_installment="1.00")


class Migration(migrations.Migration):
    dependencies = [("hubtel_direct", "0009_reserve_suspended_wallet_mandates")]

    operations = [
        migrations.AlterField(
            model_name="onlinedebtsettings", name="plan_creation_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name="onlinedebtsettings", name="allow_daily",
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name="onlinedebtsettings", name="allow_yearly",
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name="onlinedebtsettings", name="min_open_balance",
            field=models.DecimalField(decimal_places=2, max_digits=14, default="1.00"),
        ),
        migrations.AlterField(
            model_name="onlinedebtsettings", name="min_installment",
            field=models.DecimalField(decimal_places=2, max_digits=14, default="1.00"),
        ),
        migrations.RunPython(enable_agreement_drafting, migrations.RunPython.noop),
    ]
