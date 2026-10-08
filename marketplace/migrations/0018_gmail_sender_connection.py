from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0017_google_identity")]

    operations = [
        migrations.CreateModel(
            name="GmailSenderConnection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(max_length=254)),
                ("google_subject", models.CharField(max_length=255)),
                ("encrypted_refresh_token", models.TextField(editable=False)),
                ("connected_by_id", models.PositiveBigIntegerField()),
                ("connected_at", models.DateTimeField(auto_now=True)),
                ("last_send_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"verbose_name": "Authorised business Gmail sender"},
        ),
    ]
