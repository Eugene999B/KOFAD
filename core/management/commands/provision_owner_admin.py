import os

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.identity import normalize_ghana_phone
from core.models import Access, Branch
from core.services import audit


class Command(BaseCommand):
    help = "Provision the named KOFAD owner as a full administrator from deployment variables."

    def add_arguments(self, parser):
        parser.add_argument("--confirm-owner-admin", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        if not options["confirm_owner_admin"]:
            raise CommandError("This command requires --confirm-owner-admin.")

        name = os.environ.get("KOFAD_OWNER_ADMIN_NAME", "").strip()
        phone_raw = os.environ.get("KOFAD_OWNER_ADMIN_PHONE", "").strip()
        password = os.environ.get("KOFAD_OWNER_ADMIN_INITIAL_PASSWORD", "")
        if not phone_raw:
            self.stdout.write("Owner admin phone is not configured; nothing to provision.")
            return
        if not name:
            raise CommandError("Set KOFAD_OWNER_ADMIN_NAME.")
        canonical_phone = normalize_ghana_phone(phone_raw)
        username = "0" + canonical_phone[4:]
        by_username = User.objects.filter(username__iexact=username).first()
        by_phone = User.objects.filter(access__recovery_phone=canonical_phone).first()
        if by_username and by_phone and by_username.pk != by_phone.pk:
            raise CommandError("Owner username and phone already belong to different accounts.")

        user = by_username or by_phone
        created = user is None
        parts = name.split(maxsplit=1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""

        if created:
            if not password:
                raise CommandError("Set KOFAD_OWNER_ADMIN_INITIAL_PASSWORD for first-time provisioning.")
            user = User.objects.create_user(
                username=username,
                password=password,
                first_name=first_name,
                last_name=last_name,
                is_active=True,
                is_staff=True,
                is_superuser=True,
            )
        else:
            # A deployment is never a permission-repair or password-reset tool.
            # In particular, a disabled/restricted owner must not be promoted
            # back to superuser simply because a legacy variable remains set.
            if not (user.is_active and user.is_staff and user.is_superuser):
                raise CommandError(
                    "Existing owner account is disabled or not an active superuser. "
                    "Deployment cannot restore privileges; use audited administrator recovery."
                )
            # Existing account identity, recovery phone, access scope and MFA
            # are owned by authenticated administration, not stale variables.
            self.stdout.write(
                "Existing owner administrator verified; all account settings preserved."
            )
            return

        access, _ = Access.objects.get_or_create(user=user)
        access.recovery_phone = canonical_phone
        access.force_password_change = True
        access.save(update_fields=["recovery_phone", "force_password_change"])
        access.branches.set(Branch.objects.filter(active=True))

        audit(
            user,
            None,
            "admin.owner_provisioned" if created else "admin.owner_verified",
            user.pk,
            {
                "username": username,
                "name": name,
                "phone": canonical_phone,
                "force_password_change": access.force_password_change,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(
                f"Owner administrator {username} created with full access. A password change is required at first login."
            ))
