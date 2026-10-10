"""Separate native account-linked cart; no checkout or order changes."""
from django.db import migrations, models
import django.db.models.deletion
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0025_native_mobile_identity")]

    operations = [
        migrations.CreateModel(
            name="NativeCartItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.PositiveSmallIntegerField(
                    validators=[django.core.validators.MinValueValidator(1),
                                django.core.validators.MaxValueValidator(20)])),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name="mobile_cart_items", to="marketplace.customeraccount")),
                ("listing", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name="+", to="marketplace.marketlisting")),
            ],
            options={"constraints": [models.UniqueConstraint(
                fields=("customer", "listing"), name="unique_kofad_mobile_cart_line"
            )]},
        ),
    ]
