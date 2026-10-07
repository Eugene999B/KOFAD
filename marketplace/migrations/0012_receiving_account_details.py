from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0011_hubtel_checkout")]
    operations = [
        migrations.AddField(model_name="paymentconfiguration", name=name, field=models.CharField(max_length=length, blank=True))
        for name, length in [
            ("bank_account_name", 140), ("bank_account_number", 40), ("bank_name", 100),
            ("bank_branch", 100), ("bank_branch_code", 20), ("receiving_momo", 20),
        ]
    ]
