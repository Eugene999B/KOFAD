from django.db import migrations, models


def forwards(apps, schema_editor):
    DebtSettings = apps.get_model("core", "DebtSettings")
    CommunicationSettings = apps.get_model("core", "CommunicationSettings")
    Message = apps.get_model("core", "Message")
    Permission = apps.get_model("auth", "Permission")

    Permission.objects.filter(
        codename="send_messages",
        content_type__app_label="core",
        content_type__model="message",
    ).update(name="Send and retry customer SMS")

    DebtSettings.objects.filter(delivery_mode="queue").update(delivery_mode="send")
    CommunicationSettings.objects.filter(sale_receipt_mode="queue").update(sale_receipt_mode="send")
    CommunicationSettings.objects.filter(payment_confirmation_mode="queue").update(payment_confirmation_mode="send")
    CommunicationSettings.objects.filter(low_stock_mode="queue").update(low_stock_mode="send")
    CommunicationSettings.objects.filter(daily_closing_mode__in=["draft", "queue"]).update(daily_closing_mode="send")
    Message.objects.filter(status__in=["queued", "retry_wait"]).update(
        status="failed",
        last_error="Previous pending SMS was cancelled during the direct-send upgrade. Retry to send it directly.",
    )


def backwards(apps, schema_editor):
    DebtSettings = apps.get_model("core", "DebtSettings")
    CommunicationSettings = apps.get_model("core", "CommunicationSettings")

    DebtSettings.objects.filter(delivery_mode="send").update(delivery_mode="queue")
    CommunicationSettings.objects.filter(sale_receipt_mode="send").update(sale_receipt_mode="queue")
    CommunicationSettings.objects.filter(payment_confirmation_mode="send").update(payment_confirmation_mode="queue")
    CommunicationSettings.objects.filter(low_stock_mode="send").update(low_stock_mode="queue")
    CommunicationSettings.objects.filter(daily_closing_mode="send").update(daily_closing_mode="queue")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0016_message_direct_recipient"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="message",
            options={
                "ordering": ["-created_at"],
                "permissions": [("send_messages", "Send and retry customer SMS")],
            },
        ),
        migrations.RenameField(
            model_name="message",
            old_name="queued_by",
            new_name="submitted_by",
        ),
        migrations.RemoveField(
            model_name="message",
            name="next_attempt_at",
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RunSQL(
            """
            CREATE OR REPLACE FUNCTION kofad_protect_message_content()
            RETURNS trigger LANGUAGE plpgsql AS $
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'Message history cannot be deleted';
                END IF;
                IF OLD.status <> 'draft' AND (
                   NEW.body IS DISTINCT FROM OLD.body OR NEW.recipient IS DISTINCT FROM OLD.recipient
                   OR NEW.party_id IS DISTINCT FROM OLD.party_id OR NEW.branch_id IS DISTINCT FROM OLD.branch_id
                   OR NEW.channel IS DISTINCT FROM OLD.channel OR NEW.provider IS DISTINCT FROM OLD.provider
                   OR NEW.sender IS DISTINCT FROM OLD.sender OR NEW.sandbox IS DISTINCT FROM OLD.sandbox) THEN
                    RAISE EXCEPTION 'Sent message content and routing are immutable';
                END IF;
                RETURN NEW;
            END;
            $;
            """,
            """
            CREATE OR REPLACE FUNCTION kofad_protect_message_content()
            RETURNS trigger LANGUAGE plpgsql AS $
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'Message history cannot be deleted';
                END IF;
                IF OLD.status <> 'draft' AND (
                   NEW.body IS DISTINCT FROM OLD.body OR NEW.recipient IS DISTINCT FROM OLD.recipient
                   OR NEW.party_id IS DISTINCT FROM OLD.party_id OR NEW.branch_id IS DISTINCT FROM OLD.branch_id
                   OR NEW.channel IS DISTINCT FROM OLD.channel OR NEW.provider IS DISTINCT FROM OLD.provider
                   OR NEW.sender IS DISTINCT FROM OLD.sender OR NEW.sandbox IS DISTINCT FROM OLD.sandbox) THEN
                    RAISE EXCEPTION 'Queued message content and routing are immutable';
                END IF;
                RETURN NEW;
            END;
            $;
            """,
        ),
        migrations.AlterField(
            model_name="communicationsettings",
            name="daily_closing_mode",
            field=models.CharField(
                choices=[
                    ("off", "Off"),
                    ("draft", "Prepare drafts"),
                    ("send", "Send SMS immediately"),
                ],
                default="send",
                max_length=8,
            ),
        ),
        migrations.AlterField(
            model_name="communicationsettings",
            name="low_stock_mode",
            field=models.CharField(
                choices=[
                    ("off", "Off"),
                    ("draft", "Prepare drafts"),
                    ("send", "Send SMS immediately"),
                ],
                default="off",
                max_length=8,
            ),
        ),
        migrations.AlterField(
            model_name="communicationsettings",
            name="payment_confirmation_mode",
            field=models.CharField(
                choices=[
                    ("off", "Off"),
                    ("draft", "Prepare drafts"),
                    ("send", "Send SMS immediately"),
                ],
                default="off",
                max_length=8,
            ),
        ),
        migrations.AlterField(
            model_name="communicationsettings",
            name="sale_receipt_mode",
            field=models.CharField(
                choices=[
                    ("off", "Off"),
                    ("draft", "Prepare drafts"),
                    ("send", "Send SMS immediately"),
                ],
                default="off",
                max_length=8,
            ),
        ),
        migrations.AlterField(
            model_name="debtsettings",
            name="delivery_mode",
            field=models.CharField(
                choices=[
                    ("off", "Off"),
                    ("draft", "Prepare drafts"),
                    ("send", "Send SMS immediately"),
                ],
                default="off",
                max_length=8,
            ),
        ),
    ]
