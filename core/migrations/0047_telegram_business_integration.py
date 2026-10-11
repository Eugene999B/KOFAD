import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("core", "0046_verified_owner_email_reports")]

    operations = [
        migrations.CreateModel(
            name="TelegramIdentity",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("bot_kind", models.CharField(max_length=12, default="official", choices=[("official", "Official/security"), ("customer", "Customer"), ("staff", "Staff")])),
                ("kind", models.CharField(max_length=12, choices=[("staff", "Staff"), ("customer", "Customer")])),
                ("owner_id", models.PositiveBigIntegerField()),
                ("telegram_user_id", models.BigIntegerField()),
                ("chat_id", models.BigIntegerField()),
                ("auth_stamp", models.CharField(default="", max_length=64)),
                ("notifications_enabled", models.BooleanField(default=False)),
                ("marketing_opt_in", models.BooleanField(default=False)),
                ("closing_opt_in", models.BooleanField(default=False)),
                ("critical_opt_in", models.BooleanField(default=False)),
                ("stock_opt_in", models.BooleanField(default=False)),
                ("recovery_opt_in", models.BooleanField(default=False)),
                ("verified_at", models.DateTimeField()),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "indexes": [models.Index(fields=["kind", "owner_id"], name="tg_account_lookup")],
                "constraints": [models.UniqueConstraint(fields=["bot_kind", "kind", "owner_id"], name="tg_one_account_per_bot"), models.UniqueConstraint(fields=["bot_kind", "telegram_user_id"], name="tg_one_telegram_user_per_bot"), models.UniqueConstraint(fields=["bot_kind", "chat_id"], name="tg_one_telegram_chat_per_bot")],
            },
        ),
        migrations.CreateModel(
            name="TelegramLinkChallenge",
            fields=[
                ("bot_kind", models.CharField(max_length=12, default="official")),
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(max_length=12)),
                ("owner_id", models.PositiveBigIntegerField()),
                ("token_digest", models.CharField(max_length=64, unique=True)),
                ("confirmation_digest", models.CharField(blank=True, max_length=64)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("telegram_user_id", models.BigIntegerField(blank=True, null=True)),
                ("chat_id", models.BigIntegerField(blank=True, null=True)),
                ("expires_at", models.DateTimeField()),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name="TelegramUpdate",
            fields=[
                ("bot_kind", models.CharField(max_length=12, default="official")),
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("update_id", models.BigIntegerField()),
                ("received_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"constraints": [models.UniqueConstraint(fields=["bot_kind", "update_id"], name="tg_webhook_update_per_bot")]},
        ),
        migrations.CreateModel(
            name="TelegramOutbound",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_key", models.CharField(max_length=180, unique=True)),
                ("category", models.CharField(max_length=24)),
                ("body", models.TextField()),
                ("status", models.CharField(default="queued", max_length=16)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, max_length=180)),
                ("identity", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="core.telegramidentity")),
                ("branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="core.branch")),
            ],
        ),
        migrations.CreateModel(
            name="TelegramRecoveryChallenge",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("kind", models.CharField(max_length=12)),
                ("owner_id", models.PositiveBigIntegerField()),
                ("code_digest", models.CharField(max_length=64)),
                ("password_stamp", models.CharField(max_length=64)),
                ("expires_at", models.DateTimeField()),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("identity", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="core.telegramidentity")),
            ],
        ),
    ]
