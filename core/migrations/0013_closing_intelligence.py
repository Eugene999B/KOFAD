from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0012_transfer_loss_integrity")]

    operations = [
        migrations.AddField(
            model_name="closing",
            name="opening_cash",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14, validators=[MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="closing",
            name="cash_in",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14, validators=[MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="closing",
            name="cash_out",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14, validators=[MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="closing",
            name="summary",
            field=models.JSONField(default=dict),
        ),
    ]
