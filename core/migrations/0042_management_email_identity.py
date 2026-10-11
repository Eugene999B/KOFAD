"""Make the company management identity role-based without losing mailbox history.

Preserve the original primary key, foreign-key relations and employee grants.
External mail still addressed to the former management alias is handled by
the KOFAD ingestion compatibility rule.
"""
from django.db import migrations


OLD = "eugene@kofadimpex.com"
NEW = "management@kofadimpex.com"


def promote_management_mailbox(apps, schema_editor):
    Mailbox = apps.get_model("core", "EmailMailbox")
    old = Mailbox.objects.filter(address=OLD).first()
    current = Mailbox.objects.filter(address=NEW).first()
    if old and current and old.pk != current.pk:
        # Never automatically combine permission grants, confidential messages,
        # branch scopes or customer conversations from independent mailboxes.
        raise RuntimeError(
            "Both management and previous management mailboxes exist. "
            "An administrator must review the two mailboxes before merging them."
        )
    if old:
        old.address = NEW
        old.label = "Management"
        old.save(update_fields=["address", "label"])
    elif current:
        if current.label != "Management":
            current.label = "Management"
            current.save(update_fields=["label"])
    else:
        Mailbox.objects.create(address=NEW, label="Management")


class Migration(migrations.Migration):
    dependencies = [("core", "0041_email_delivery_events")]

    operations = [
        migrations.RunPython(promote_management_mailbox, migrations.RunPython.noop),
    ]
