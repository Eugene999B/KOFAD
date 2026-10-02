import hashlib
from django.db import migrations


def clear_initial_lockout(apps, schema_editor):
    Access = apps.get_model("core", "Access")
    LoginAttempt = apps.get_model("core", "LoginAttempt")
    Audit = apps.get_model("core", "Audit")
    for access in Access.objects.filter(user__username__iexact="ADMIN", must_change_password=True).select_related("user"):
        key = hashlib.sha256(access.user.username.casefold().encode()).hexdigest()
        cleared = LoginAttempt.objects.filter(key=key).update(failures=0, blocked_until=None)
        Audit.objects.create(actor_id=access.user_id, action="initial_access.repaired", reference=str(access.user_id),
            detail={"setup_key_removed": True, "lockout_records_cleared": cleared, "password_changed": False})


class Migration(migrations.Migration):
    dependencies = [("core", "0007_transfer_receipts")]
    operations = [migrations.RunPython(clear_initial_lockout, migrations.RunPython.noop)]
