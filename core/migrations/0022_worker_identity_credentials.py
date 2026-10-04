from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0021_whatsapp_cloud_production"),
    ]

    operations = [
        migrations.AddField(
            model_name="worker",
            name="blood_group",
            field=models.CharField(blank=True, max_length=12),
        ),
        migrations.AddField(
            model_name="worker",
            name="id_card_issue_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="worker",
            name="id_card_expiry_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
