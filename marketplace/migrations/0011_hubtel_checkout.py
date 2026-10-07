from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0010_phone_change_purpose")]
    operations = [
        migrations.CreateModel(name="PaymentConfiguration", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("provider", models.CharField(choices=[("paystack", "Paystack"), ("hubtel", "Hubtel")], default="paystack", max_length=24)),
        ]),
        migrations.AddField(model_name="marketpaymentattempt", name="next_check_at", field=models.DateTimeField(blank=True, db_index=True, null=True)),
        migrations.AddField(model_name="marketpaymentattempt", name="check_count", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="marketpaymentattempt", name="verification_summary", field=models.JSONField(blank=True, default=dict)),
    ]
