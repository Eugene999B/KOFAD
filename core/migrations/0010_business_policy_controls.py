# Generated for KOFAD business policy controls.
import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0009_account_recovery")]

    operations = [
        migrations.AddField(
            model_name="company", name="payment_cash",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="company", name="payment_momo",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="company", name="payment_bank",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="company", name="payment_card",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="company", name="allow_discounts",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="company", name="staff_discount_limit",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=5,
                validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(100)]),
        ),
        migrations.AddField(
            model_name="company", name="max_discount_percent",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=5,
                validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(100)]),
        ),
        migrations.AddField(
            model_name="company", name="allow_price_overrides",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="company", name="staff_price_reduction_limit",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=5,
                validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(100)]),
        ),
        migrations.AddField(
            model_name="company", name="max_price_reduction_percent",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=5,
                validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(100)]),
        ),
        migrations.AddField(
            model_name="company", name="allow_credit_sales",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="company", name="max_credit_days",
            field=models.PositiveIntegerField(default=90, validators=[django.core.validators.MinValueValidator(1)]),
        ),
        migrations.AddField(
            model_name="company", name="max_credit_override",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14,
                validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="company", name="customer_required_above",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14,
                validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="company", name="sale_manager_threshold",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14,
                validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="company", name="expense_manager_threshold",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14,
                validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="company", name="reference_prefix",
            field=models.CharField(blank=True, max_length=8),
        ),
        migrations.AddField(
            model_name="company", name="receipt_show_staff",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="company", name="receipt_show_contact_phone",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="company", name="receipt_show_payment_reference",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="line", name="list_price",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="line", name="discount_percent",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=5,
                validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(100)]),
        ),
        migrations.AddConstraint(
            model_name="company",
            constraint=models.CheckConstraint(
                condition=models.Q(staff_discount_limit__lte=models.F("max_discount_percent")),
                name="company_discount_limits_ordered",
            ),
        ),
        migrations.AddConstraint(
            model_name="company",
            constraint=models.CheckConstraint(
                condition=models.Q(staff_price_reduction_limit__lte=models.F("max_price_reduction_percent")),
                name="company_price_limits_ordered",
            ),
        ),
        migrations.AddConstraint(
            model_name="line",
            constraint=models.CheckConstraint(
                condition=models.Q(discount_percent__gte=0, discount_percent__lte=100),
                name="valid_line_discount",
            ),
        ),
    ]
