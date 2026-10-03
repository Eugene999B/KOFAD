from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0015_access_force_password_change"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="recipient_name",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="message",
            name="manual_override",
            field=models.BooleanField(default=False),
        ),
        migrations.RemoveConstraint(
            model_name="message",
            name="message_exactly_one_recipient",
        ),
        migrations.AddConstraint(
            model_name="message",
            constraint=models.CheckConstraint(
                condition=(
                    Q(party__isnull=False, management_contact__isnull=True)
                    | Q(party__isnull=True, management_contact__isnull=False)
                    | (
                        Q(
                            party__isnull=True,
                            management_contact__isnull=True,
                            manual_override=True,
                        )
                        & ~Q(recipient="")
                    )
                ),
                name="message_has_recipient",
            ),
        ),
    ]
