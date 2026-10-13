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
        migrations.RunPython(enable_agreement_drafting, migrations.RunPython.noop),
    ]
