from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0021_marketing_email_consent")]
    operations = [
        migrations.AlterField(
            model_name="otpthrottle", name="purpose",
            field=models.CharField(
                choices=[
                    ("register", "Register"), ("reset", "Reset password"),
                    ("login", "Customer login"), ("change_phone", "Change phone"),
                    ("momo", "First Mobile Money payment"),
                ],
                max_length=12,
            ),
        ),
        migrations.CreateModel(
            name="VerifiedMomoPhone",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("phone", models.CharField(max_length=20)),
                ("verified_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="verified_momo_phones", to="marketplace.customeraccount")),
            ],
        ),
        migrations.AddConstraint(
            model_name="verifiedmomophone",
            constraint=models.UniqueConstraint(fields=("customer", "phone"), name="one_verified_momo_phone_per_customer"),
        ),
    ]
