from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0016_email_identity_and_outbox")]

    operations = [
        migrations.CreateModel(
            name="GoogleIdentity",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("staff", "Staff"), ("customer", "Customer")], max_length=12)),
                ("owner_id", models.PositiveBigIntegerField()),
                ("subject", models.CharField(max_length=255)),
                ("email", models.EmailField(max_length=254)),
                ("linked_at", models.DateTimeField(auto_now_add=True)),
                ("last_login_at", models.DateTimeField(blank=True, null=True)),
            ],
        ),
        migrations.AddConstraint(
            model_name="googleidentity",
            constraint=models.UniqueConstraint(fields=("kind", "owner_id"), name="kofad_google_one_owner"),
        ),
        migrations.AddConstraint(
            model_name="googleidentity",
            constraint=models.UniqueConstraint(fields=("kind", "subject"), name="kofad_google_unique_subject"),
        ),
    ]
