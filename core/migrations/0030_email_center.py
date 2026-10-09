from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Q


def create_mailboxes(apps, schema_editor):
    Mailbox = apps.get_model("core", "EmailMailbox")
    for name, label in [
        ("transactions", "Transactions"),
        ("business", "Business"),
        ("eugene", "Management"),
        ("info", "Information"),
        ("support", "Customer Support"),
        ("sales", "Sales"),
        ("accounts", "Accounts"),
        ("orders", "Orders"),
        ("staff", "Staff"),
        ("reports", "Business Reports"),
    ]:
        Mailbox.objects.get_or_create(address=f"{name}@kofadimpex.com", defaults={"label": label})


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0029_staff_invitation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EmailMailbox",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False, auto_created=True, verbose_name="ID")),
                ("address", models.EmailField(max_length=254, unique=True)),
                ("label", models.CharField(max_length=100)),
                ("active", models.BooleanField(default=True)),
                ("branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="core.branch")),
            ],
            options={"ordering": ["address"]},
        ),
        migrations.CreateModel(
            name="EmailMailboxMember",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False, auto_created=True, verbose_name="ID")),
                ("can_read", models.BooleanField(default=True)),
                ("can_send", models.BooleanField(default=False)),
                ("mailbox", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="members", to="core.emailmailbox")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="kofad_mailbox_memberships", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="EmailLetter",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False, auto_created=True, verbose_name="ID")),
                ("direction", models.CharField(choices=[("inbound", "Incoming"), ("outbound", "Outgoing")], max_length=8)),
                ("status", models.CharField(choices=[("received", "Received"), ("queued", "Queued"), ("sending", "Sending"), ("submitted", "Submitted to provider"), ("internal", "Delivered internally"), ("failed", "Failed"), ("uncertain", "Needs review")], max_length=12)),
                ("from_address", models.EmailField(max_length=254)),
                ("to_address", models.EmailField(max_length=254)),
                ("subject", models.CharField(blank=True, max_length=255)),
                ("body_text", models.TextField(blank=True)),
                ("message_id", models.CharField(blank=True, max_length=255)),
                ("in_reply_to", models.CharField(blank=True, max_length=255)),
                ("fingerprint", models.CharField(blank=True, max_length=64)),
                ("source_key", models.CharField(blank=True, max_length=180, null=True, unique=True)),
                ("had_attachments", models.BooleanField(default=False)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, max_length=160)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="kofad_sent_letters", to=settings.AUTH_USER_MODEL)),
                ("mailbox", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="letters", to="core.emailmailbox")),
            ],
            options={"ordering": ["-created_at", "-pk"]},
        ),
        migrations.AddConstraint(model_name="emailmailboxmember", constraint=models.UniqueConstraint(fields=("mailbox", "user"), name="unique_kofad_mailbox_member")),
        migrations.AddConstraint(model_name="emailletter", constraint=models.UniqueConstraint(fields=("mailbox", "fingerprint"), condition=Q(direction="inbound"), name="unique_kofad_inbound_fingerprint")),
        migrations.AddIndex(model_name="emailletter", index=models.Index(fields=["status", "next_attempt_at"], name="kofad_mail_delivery")),
        migrations.AddIndex(model_name="emailletter", index=models.Index(fields=["mailbox", "direction", "created_at"], name="kofad_mail_list")),
        migrations.RunPython(create_mailboxes, migrations.RunPython.noop),
    ]
