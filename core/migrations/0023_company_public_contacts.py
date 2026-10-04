from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0022_worker_identity_credentials")]

    operations = [
        migrations.AddField(
            model_name="company",
            name="email",
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AddField(
            model_name="company",
            name="whatsapp_phone",
            field=models.CharField(blank=True, max_length=40),
        ),
    ]
