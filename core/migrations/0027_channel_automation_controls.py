from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0026_message_archive_state")]
    operations = [
        migrations.AddField(model_name="communicationsettings", name="whatsapp_sale_receipt_mode", field=models.CharField(choices=[("off", "Off"), ("draft", "Prepare drafts"), ("send", "Send SMS immediately")], default="off", max_length=8)),
        migrations.AddField(model_name="communicationsettings", name="whatsapp_payment_confirmation_mode", field=models.CharField(choices=[("off", "Off"), ("draft", "Prepare drafts"), ("send", "Send SMS immediately")], default="off", max_length=8)),
        migrations.AddField(model_name="communicationsettings", name="whatsapp_daily_closing_mode", field=models.CharField(choices=[("off", "Off"), ("draft", "Prepare drafts"), ("send", "Send SMS immediately")], default="off", max_length=8)),
        migrations.AddField(model_name="communicationsettings", name="whatsapp_low_stock_mode", field=models.CharField(choices=[("off", "Off"), ("draft", "Prepare drafts"), ("send", "Send SMS immediately")], default="off", max_length=8)),
        migrations.AddField(model_name="communicationsettings", name="whatsapp_debt_reminder_mode", field=models.CharField(choices=[("off", "Off"), ("draft", "Prepare drafts"), ("send", "Send SMS immediately")], default="off", max_length=8)),
        migrations.AddField(model_name="communicationsettings", name="whatsapp_template_name", field=models.CharField(blank=True, max_length=120)),
        migrations.AddField(model_name="communicationsettings", name="whatsapp_template_language", field=models.CharField(default="en", max_length=12)),
    ]
