"""Retire the management department without destroying business correspondence.

The management mailbox is deleted only if there are no protected messages,
threads, staff drafts or saved replies. Otherwise its inactive database record
remains as a legal/audit tombstone; it is absent from all active KOFAD inboxes.
This migration is intentionally one-way: never recreate a retired mailbox
or reenable permissions on reverse migration.
"""
from django.db import migrations
from django.db.models.deletion import ProtectedError

RETIRED = ("management@kofadimpex.com", "eugene@kofadimpex.com")


def retire_management(apps, schema_editor):
    Mailbox = apps.get_model("core", "EmailMailbox")
    Letter = apps.get_model("core", "EmailLetter")
    Conversation = apps.get_model("core", "EmailConversation")
    Draft = apps.get_model("core", "EmailStaffDraft")
    SavedReply = apps.get_model("core", "EmailSavedReply")
    Member = apps.get_model("core", "EmailMailboxMember")
    for mailbox in Mailbox.objects.filter(address__in=RETIRED):
        # Disable first so even an old deployed worker cannot pick up this
        # identity through a normal active-mailbox query.
        if mailbox.active:
            mailbox.active = False
            mailbox.save(update_fields=["active"])
        # No unsent management email may leave through an old outbox row.
        Letter.objects.filter(
            mailbox_id=mailbox.pk, direction="outbound",
            status__in=["queued", "failed", "draft"],
        ).update(
            status="suppressed",
            last_error="Management email account retired at owner's request.",
        )
        Member.objects.filter(mailbox_id=mailbox.pk).delete()
        # Messages, customer threads, drafts and shared replies are customer/
        # staff records. Never remove them via a cascading mailbox deletion.
        historical = (
            Letter.objects.filter(mailbox_id=mailbox.pk).exists()
            or Conversation.objects.filter(mailbox_id=mailbox.pk).exists()
            or Draft.objects.filter(mailbox_id=mailbox.pk).exists()
            or SavedReply.objects.filter(mailbox_id=mailbox.pk).exists()
        )
        if not historical:
            try:
                mailbox.delete()
            except ProtectedError:
                # An unanticipated protected relation is still important data;
                # keep the inactive mailbox for integrity instead of failing
                # or deleting data without knowing what it contains.
                pass


class Migration(migrations.Migration):
    dependencies = [("core", "0042_management_email_identity")]

    operations = [
        migrations.RunPython(retire_management, migrations.RunPython.noop),
    ]
