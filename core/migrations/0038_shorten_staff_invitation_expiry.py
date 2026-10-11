"""Shorten all previously issued, still-unconsumed 24h staff grants to one hour.

All existing invites were issued by staff_invites.issue() using expires_at =
issuance + 24h, including renewed links (created_at is NOT updated on renewal).
Subtracting 23h from their stored deadline enforces a one-hour lifetime
without granting any old, leaked invitation more time.
"""
from datetime import timedelta

from django.db import migrations
from django.db.models import F


def shorten_legacy_invites(apps, schema_editor):
    StaffInvitation = apps.get_model("core", "StaffInvitation")
    StaffInvitation.objects.using(schema_editor.connection.alias).filter(
        consumed_at__isnull=True,
    ).update(expires_at=F("expires_at") - timedelta(hours=23))


class Migration(migrations.Migration):
    dependencies = [("core", "0037_email_conversations")]

    operations = [
        # Do NOT lengthen the lifetime of links during a rollback.
        migrations.RunPython(shorten_legacy_invites, reverse_code=migrations.RunPython.noop),
    ]
