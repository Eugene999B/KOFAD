from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0024_company_delivery_policy"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="expense_funding_source",
            field=models.CharField(blank=True, default="today_sales_receipts", max_length=40),
        ),
        migrations.AddField(
            model_name="document",
            name="expense_affects_daily_closing",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="document",
            name="expense_funding_note",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
    ]
