from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0005_paystack_refund_lifecycle")]

    operations = [
        migrations.AddField(
            model_name="otpthrottle",
            name="code_digest",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
