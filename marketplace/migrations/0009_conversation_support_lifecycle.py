from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("marketplace", "0008_onlineorder_delivery_route"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="accepted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="conversation",
            name="closed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="conversation",
            name="closed_reason",
            field=models.CharField(blank=True, default="", max_length=24),
        ),
    ]
