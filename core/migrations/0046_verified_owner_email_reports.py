from django.db import migrations


def enable_verified_owner_reports(apps, schema_editor):
    """Seed management reporting only for already verified, opted-in owner mailboxes.

    No SMS subscriptions or customer marketing consent is created here.
    """
    User = apps.get_model("auth", "User")
    Access = apps.get_model("core", "Access")
    EmailIdentity = apps.get_model("marketplace", "EmailIdentity")
    for identity in EmailIdentity.objects.filter(
        kind="staff", verified_at__isnull=False, notifications_enabled=True
    ).exclude(email="").iterator():
        if User.objects.filter(
            pk=identity.owner_id, is_superuser=True, is_active=True,
            email__iexact=identity.email,
        ).exists():
            Access.objects.filter(user_id=identity.owner_id).update(
                email_daily_closing=True,
                email_weekly_review=True,
                email_monthly_review=True,
                email_critical_alerts=True,
            )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0045_merge_staff_notifications"),
        ("marketplace", "0026_merge_customer_email"),
    ]

    operations = [
        migrations.RunPython(enable_verified_owner_reports, migrations.RunPython.noop),
    ]
