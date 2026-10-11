from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0036_debt_email_and_review"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="EmailConversation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("customer_email", models.EmailField(max_length=254)),
                ("subject", models.CharField(max_length=255)),
                ("status", models.CharField(choices=[("open", "Open"), ("pending", "Waiting for customer"),
                                                    ("closed", "Closed")], default="open", max_length=8)),
                ("priority", models.CharField(choices=[("normal", "Normal"), ("high", "High")],
                                              default="normal", max_length=8)),
                ("last_activity_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_customer_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("mailbox", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                             related_name="conversations", to="core.emailmailbox")),
                ("assigned_to", models.ForeignKey(blank=True, null=True,
                                                   on_delete=django.db.models.deletion.SET_NULL,
                                                   related_name="kofad_assigned_conversations",
                                                   to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-last_activity_at", "-pk"]},
        ),
        migrations.CreateModel(
            name="EmailConversationNote",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("body", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("author", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                            to=settings.AUTH_USER_MODEL)),
                ("conversation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                                   related_name="notes", to="core.emailconversation")),
            ],
            options={"ordering": ["created_at", "pk"]},
        ),
        migrations.AddField(
            model_name="emailletter", name="conversation",
            field=models.ForeignKey(blank=True, null=True,
                                    on_delete=django.db.models.deletion.SET_NULL,
                                    related_name="letters", to="core.emailconversation"),
        ),
        migrations.AddIndex(
            model_name="emailconversation",
            index=models.Index(fields=["mailbox", "status", "last_activity_at"],
                               name="kofad_thread_listing"),
        ),
        migrations.AddIndex(
            model_name="emailconversation",
            index=models.Index(fields=["mailbox", "customer_email"],
                               name="kofad_thread_customer"),
        ),
    ]
