from datetime import time

from django.core.validators import MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


def seed_settings(apps, schema_editor):
    DebtSettings = apps.get_model("core", "DebtSettings")
    CommunicationSettings = apps.get_model("core", "CommunicationSettings")
    DebtSettings.objects.get_or_create(pk=1)
    CommunicationSettings.objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [("core", "0013_closing_intelligence")]

    operations = [
        migrations.AddField(
            model_name="company",
            name="secondary_phone",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.CreateModel(
            name="DebtSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("delivery_mode", models.CharField(choices=[("off", "Off"), ("draft", "Prepare drafts"), ("queue", "Queue SMS automatically")], default="off", max_length=8)),
                ("reminder_time", models.TimeField(default=time(9, 0))),
                ("due_soon_enabled", models.BooleanField(default=True)),
                ("due_soon_days", models.CharField(default="7,3,1", max_length=80)),
                ("due_today_enabled", models.BooleanField(default=True)),
                ("overdue_enabled", models.BooleanField(default=True)),
                ("overdue_grace_value", models.PositiveIntegerField(default=0)),
                ("overdue_grace_unit", models.CharField(choices=[("days", "Days"), ("weeks", "Weeks"), ("months", "Months (30 days)")], default="days", max_length=8)),
                ("overdue_repeat_days", models.PositiveIntegerField(default=3, validators=[MinValueValidator(1)])),
                ("max_sms_7_days", models.PositiveIntegerField(default=3, validators=[MinValueValidator(1)])),
                ("max_sms_30_days", models.PositiveIntegerField(default=8, validators=[MinValueValidator(1)])),
                ("minimum_hours_between_sms", models.PositiveIntegerField(default=24, validators=[MinValueValidator(1)])),
                ("minimum_balance", models.DecimalField(decimal_places=2, default=1, max_digits=14, validators=[MinValueValidator(0)])),
                ("skip_weekends", models.BooleanField(default=False)),
                ("message_template", models.TextField(default="{company}: Dear {customer}, your outstanding balance is {currency} {balance} across {debt_count} receipt(s). {due_sentence} Please pay or contact us on {business_phone}. Thank you.")),
            ],
        ),
        migrations.CreateModel(
            name="CommunicationSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sale_receipt_mode", models.CharField(choices=[("off", "Off"), ("draft", "Prepare drafts"), ("queue", "Queue SMS automatically")], default="off", max_length=8)),
                ("payment_confirmation_mode", models.CharField(choices=[("off", "Off"), ("draft", "Prepare drafts"), ("queue", "Queue SMS automatically")], default="off", max_length=8)),
                ("daily_closing_mode", models.CharField(choices=[("off", "Off"), ("draft", "Prepare drafts"), ("queue", "Queue SMS automatically")], default="draft", max_length=8)),
                ("low_stock_mode", models.CharField(choices=[("off", "Off"), ("draft", "Prepare drafts"), ("queue", "Queue SMS automatically")], default="off", max_length=8)),
                ("low_stock_time", models.TimeField(default=time(17, 0))),
                ("closing_template", models.TextField(default="{company} closing {date} · Sales {currency} {sales_total}; cash expected {currency} {expected_cash}; cash counted {currency} {counted_cash}; variance {currency} {cash_variance}; debt collected {currency} {debt_collections}; expenses {currency} {expenses}. Submitted by {staff}.")),
                ("low_stock_template", models.TextField(default="{company} stock alert · {low_count} product(s) are low and {out_count} out of stock at {location}. Open Inventory for details.")),
            ],
        ),
        migrations.CreateModel(
            name="ManagementContact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("phone", models.CharField(max_length=20)),
                ("receive_closing", models.BooleanField(default=True)),
                ("receive_low_stock", models.BooleanField(default=False)),
                ("receive_system_alerts", models.BooleanField(default=False)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="core.branch")),
            ],
            options={"ordering": ["name", "pk"]},
        ),
        migrations.AlterField(
            model_name="message",
            name="party",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="core.party"),
        ),
        migrations.AddField(
            model_name="message",
            name="management_contact",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="core.managementcontact"),
        ),
        migrations.AddConstraint(
            model_name="message",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("party__isnull", False), ("management_contact__isnull", True)) |
                    models.Q(("party__isnull", True), ("management_contact__isnull", False))
                ),
                name="message_exactly_one_recipient",
            ),
        ),
        migrations.RunPython(seed_settings, migrations.RunPython.noop),
    ]
