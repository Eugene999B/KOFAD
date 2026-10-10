from django.db import migrations, models
import django.utils.timezone

class Migration(migrations.Migration):
    dependencies = [("core", "0040_email_copied_recipients")]
    operations = [
        migrations.CreateModel(
            name="MobileReleasePolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("channel", models.CharField(choices=[("customer", "KOFAD Market"), ("staff", "KOFAD Staff")], max_length=12, unique=True)),
                ("minimum_android_version", models.CharField(blank=True, max_length=24)),
                ("critical_update_reason", models.CharField(blank=True, max_length=220)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name_plural": "Mobile release policies"},
        ),
        migrations.CreateModel(
            name="MobileNotice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("channel", models.CharField(choices=[("customer", "KOFAD Market"), ("staff", "KOFAD Staff")], db_index=True, max_length=12)),
                ("title", models.CharField(max_length=90)),
                ("message", models.CharField(max_length=280)),
                ("priority", models.CharField(choices=[("info", "Information"), ("important", "Important"), ("urgent", "Urgent")], default="info", max_length=12)),
                ("enabled", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["-created_at", "-pk"]},
        ),
    ]
