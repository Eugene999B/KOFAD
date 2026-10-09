from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0018_gmail_sender_connection")]

    operations = [
        migrations.AlterField(
            model_name="customeraccount", name="phone",
            field=models.CharField(max_length=20, unique=True, null=True, blank=True),
        ),
    ]
