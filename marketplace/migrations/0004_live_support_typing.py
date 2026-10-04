from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0003_market_v3_live_commerce")]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="customer_typing_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="conversation",
            name="staff_typing_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
