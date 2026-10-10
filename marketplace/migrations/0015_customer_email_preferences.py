from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0014_receiving_momo_name")]
    operations = [
        migrations.AddField(model_name="customeraccount", name="transactional_email_enabled", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="customeraccount", name="marketing_email_opt_in", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="customeraccount", name="marketing_email_verified_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="customeraccount", name="marketing_email_challenge", field=models.CharField(blank=True, default="", max_length=64)),
    ]
