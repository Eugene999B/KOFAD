from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [("core", "0023_company_public_contacts")]

    operations = [
        migrations.AddField(model_name="company", name="delivery_enabled", field=models.BooleanField(default=True)),
        migrations.AddField(
            model_name="company", name="delivery_pricing_mode",
            field=models.CharField(
                choices=[("free", "Free delivery"), ("flat", "Flat delivery fee"), ("distance", "Price per kilometre")],
                default="free", max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="company", name="delivery_flat_fee",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="company", name="delivery_rate_per_km",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="company", name="delivery_minimum_fee",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="company", name="delivery_max_distance_km",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=9, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(model_name="company", name="delivery_origin_label", field=models.CharField(blank=True, default="", max_length=180)),
        migrations.AddField(model_name="company", name="delivery_origin_latitude", field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
        migrations.AddField(model_name="company", name="delivery_origin_longitude", field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
    ]
