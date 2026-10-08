from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0013_conversation_branch")]
    operations = [
        migrations.AddField(
            model_name="paymentconfiguration", name="receiving_momo_name",
            field=models.CharField(max_length=140, blank=True),
        ),
    ]
