import hashlib
import uuid
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import migrations, models
import django.db.models.deletion


def simplify_initial_access(apps, schema_editor):
    Access = apps.get_model("core", "Access")
    User = apps.get_model("auth", "User")
    Audit = apps.get_model("core", "Audit")
    Attempt = apps.get_model("core", "LoginAttempt")
    Access.objects.all().update(must_change_password=False, totp_secret="", totp_last_step=-1)
    # Explicit owner-requested one-time restoration. Future deployments never reset it.
    for user in User.objects.filter(username__iexact="ADMIN", is_superuser=True):
        user.password = make_password("ADMIN")
        user.save(update_fields=["password"])
        Access.objects.filter(user_id=user.pk).update(session_version=models.F("session_version") + 1)
        Attempt.objects.filter(key=hashlib.sha256(user.username.casefold().encode()).hexdigest()).update(failures=0, blocked_until=None)
        Audit.objects.create(actor_id=user.pk, action="admin.login_simplified", reference=str(user.pk),
            detail={"owner_requested": True, "mandatory_password_change": False, "authenticator_removed": True})


class Migration(migrations.Migration):
    dependencies = [("core", "0008_clear_initial_login_lockout"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(model_name="access", name="recovery_phone", field=models.CharField(blank=True, max_length=20)),
        migrations.CreateModel(name="PasswordRecovery", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("phone", models.CharField(max_length=20)),
            ("code_digest", models.CharField(max_length=64)),
            ("password_stamp", models.CharField(max_length=64)),
            ("expires_at", models.DateTimeField()),
            ("attempts", models.PositiveIntegerField(default=0)),
            ("used", models.BooleanField(default=False)),
            ("sent", models.BooleanField(default=False)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.RunPython(simplify_initial_access, migrations.RunPython.noop),
    ]
