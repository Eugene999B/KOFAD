from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0006_local_customer_otp_digest")]

    operations = [
        migrations.AddField(
            model_name="onlineorder",
            name="confirmed_reference",
            field=models.CharField(blank=True, max_length=32, null=True, unique=True),
        ),
    ]
