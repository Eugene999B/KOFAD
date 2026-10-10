"""Add private mail productivity state without changing existing email content."""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0038_shorten_staff_invitation_expiry"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(
            model_name="emailconversation", name="archived_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="emailconversation", name="snoozed_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="emailconversation", name="due_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="EmailConversationReadState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("last_read_at", models.DateTimeField(blank=True, null=True)),
                ("starred", models.BooleanField(default=False)),
                ("conversation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name="read_states", to="core.emailconversation")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name="email_read_states", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="emailconversationreadstate",
            constraint=models.UniqueConstraint(fields=("conversation", "user"), name="unique_mail_read_per_staff"),
        ),
        migrations.CreateModel(
            name="EmailStaffDraft",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("recipient", models.EmailField(blank=True, max_length=254)),
                ("subject", models.CharField(blank=True, max_length=255)),
                ("body", models.TextField(blank=True)),
                ("scheduled_for", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("mailbox", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="staff_drafts", to="core.emailmailbox")),
                ("author", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name="email_staff_drafts", to=settings.AUTH_USER_MODEL)),
                ("conversation", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL, to="core.emailconversation")),
            ],
            options={"ordering": ["-updated_at", "-pk"]},
        ),
        migrations.CreateModel(
            name="EmailSavedReply",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=120)),
                ("body", models.TextField()),
                ("shared", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("mailbox", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT, to="core.emailmailbox")),
                ("author", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["title", "pk"]},
        ),
        migrations.CreateModel(
            name="EmailStaffSignature",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("body", models.TextField(blank=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("mailbox", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    to="core.emailmailbox")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="emailstaffsignature",
            constraint=models.UniqueConstraint(fields=("mailbox", "user"), name="unique_email_staff_signature"),
        ),
    ]
