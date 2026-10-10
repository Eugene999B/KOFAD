from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0044_mobile_release_controls"),
    ]

    operations = [
        migrations.AddField(
            model_name="mobilenotice",
            name="kind",
            field=models.CharField(
                choices=[
                    ("service", "Service / operational notice"),
                    ("promotion", "Optional product promotion"),
                ],
                default="service",
                help_text="Tag product advertising as a promotion so customers may opt out independently.",
                max_length=12,
            ),
        ),
    ]
