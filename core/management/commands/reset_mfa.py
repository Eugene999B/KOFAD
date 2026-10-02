from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from core.models import Access
from core.services import audit


class Command(BaseCommand):
    help = "Operator-only MFA recovery. Requires host/database access. Revokes sessions and logs the reason."
    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--reason", required=True)
    @transaction.atomic
    def handle(self, *args, **options):
        if len(options["reason"].strip()) < 10:
            raise CommandError("Provide a recovery reason of at least 10 characters.")
        try:
            access = Access.objects.select_for_update().select_related("user").get(user__username=options["username"])
        except Access.DoesNotExist:
            raise CommandError("Account not found.")
        access.totp_secret = ""
        access.totp_last_step = -1
        access.session_version += 1
        access.save()
        audit(None, None, "mfa.operator_reset", access.user_id, {"reason":options["reason"]})
        self.stdout.write("MFA reset; sessions revoked. Verify the account owner's identity before sharing access.")
