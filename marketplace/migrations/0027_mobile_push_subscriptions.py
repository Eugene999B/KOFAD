from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [
        ("marketplace", "0026_native_customer_cart"),
    ]
    operations = [
        migrations.CreateModel(
            name="MobilePushSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token_digest", models.CharField(max_length=64, unique=True)),
                ("encrypted_token", models.TextField(editable=False)),
                ("service_opt_in", models.BooleanField(default=False)),
                ("marketing_opt_in", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("device_session", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="push_subscription", to="marketplace.mobiledevicesession",
                )),
            ],
        ),
    ]
