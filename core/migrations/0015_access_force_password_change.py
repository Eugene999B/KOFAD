from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0014_debt_communication_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="access",
            name="force_password_change",
            field=models.BooleanField(default=False),
        ),
    ]
