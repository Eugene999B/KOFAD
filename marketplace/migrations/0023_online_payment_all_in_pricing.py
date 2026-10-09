from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0022_verified_momo_phone")]

    operations = [
        migrations.AddField(
            model_name="paymentconfiguration",
            name="online_price_markup_percent",
            field=models.DecimalField(
                max_digits=6, decimal_places=3, default=Decimal("0"),
                validators=[MinValueValidator(0), MaxValueValidator(100)],
                help_text="Built into customer-visible Market and provider-backed POS MoMo product prices.",
            ),
        ),
    ]
