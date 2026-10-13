"""Branch-scoped, fail-closed online debt controls and durable debit send times."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("hubtel_direct", "0003_salary_transfers_refunds"),
        ("core", "0048_telegram_optional_phone_confirmation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="OnlineDebtSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("plan_creation_enabled", models.BooleanField(default=False)),
                ("pause_auto_collection", models.BooleanField(default=True)),
                ("allow_mtn", models.BooleanField(default=True)),
                ("allow_telecel", models.BooleanField(default=True)),
                ("allow_once", models.BooleanField(default=True)),
                ("allow_daily", models.BooleanField(default=False)),
                ("allow_weekly", models.BooleanField(default=True)),
                ("allow_monthly", models.BooleanField(default=True)),
                ("allow_quarterly", models.BooleanField(default=True)),
                ("allow_yearly", models.BooleanField(default=False)),
                ("default_frequency", models.CharField(max_length=12, choices=[
                    ("once", "One charge when overdue"), ("daily", "Daily"), ("weekly", "Weekly"),
                    ("monthly", "Monthly"), ("quarterly", "Quarterly"), ("yearly", "Yearly"),
                ], default="weekly")),
                ("min_open_balance", models.DecimalField(decimal_places=2, max_digits=14, default="10.00")),
                ("max_open_balance", models.DecimalField(decimal_places=2, max_digits=14, default="10000.00")),
                ("min_installment", models.DecimalField(decimal_places=2, max_digits=14, default="5.00")),
                ("max_installment", models.DecimalField(decimal_places=2, max_digits=14, default="2000.00")),
                ("min_first_due_days", models.PositiveSmallIntegerField(default=0)),
                ("max_first_due_days", models.PositiveSmallIntegerField(default=90)),
                ("auto_collection_grace_days", models.PositiveSmallIntegerField(default=0)),
                ("collection_from_hour", models.PositiveSmallIntegerField(default=8)),
                ("collection_until_hour", models.PositiveSmallIntegerField(default=19)),
                ("skip_weekends", models.BooleanField(default=True)),
                ("rollover_unpaid", models.BooleanField(default=True)),
                ("daily_collection_limit", models.DecimalField(decimal_places=2, max_digits=14, default="5000.00")),
                ("daily_attempt_limit", models.PositiveSmallIntegerField(default=20)),
                ("branch", models.OneToOneField(
                    to="core.branch", on_delete=django.db.models.deletion.PROTECT,
                    related_name="online_debt_settings",
                )),
                ("changed_by", models.ForeignKey(
                    to=settings.AUTH_USER_MODEL, null=True, blank=True,
                    related_name="+", on_delete=django.db.models.deletion.PROTECT,
                )),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "Online debt settings", "verbose_name_plural": "Online debt settings"},
        ),
        migrations.AddField(
            model_name="debtdebitattempt", name="sent_at",
            field=models.DateTimeField(null=True, blank=True, db_index=True),
        ),
    ]
