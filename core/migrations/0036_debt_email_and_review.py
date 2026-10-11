from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0035_email_campaign_queuing")]
    operations = [
        migrations.AddField(
            model_name="debtsettings", name="email_delivery_mode",
            field=models.CharField(choices=[("off", "Off"), ("draft", "Prepare for review"),
                                            ("send", "Send automatically")], default="off", max_length=8)),
        migrations.AddField(
            model_name="party", name="debt_email_opt_in",
            field=models.BooleanField(default=False)),
        migrations.AddField(
            model_name="emailletter", name="approved_by",
            field=models.ForeignKey(to=settings.AUTH_USER_MODEL, null=True, blank=True,
                                    related_name="kofad_approved_letters", on_delete=models.SET_NULL)),
        migrations.AddField(
            model_name="emailletter", name="approved_at",
            field=models.DateTimeField(null=True, blank=True)),
        migrations.AlterField(
            model_name="emailletter", name="status",
            field=models.CharField(max_length=12, choices=[
                ("received", "Received"), ("draft", "Awaiting review"), ("queued", "Queued"),
                ("sending", "Sending"), ("submitted", "Submitted to provider"),
                ("internal", "Delivered internally"), ("failed", "Failed"),
                ("uncertain", "Needs review"), ("suppressed", "Suppressed by recipient preference"),
            ])),
    ]
