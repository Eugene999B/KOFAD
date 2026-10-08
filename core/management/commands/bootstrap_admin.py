import os
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from core.models import Access, Branch
from core.services import audit


class Command(BaseCommand):
    help = "Explicit, one-time ADMIN bootstrap; creates the initial administrator with a mandatory password change."
    def add_arguments(self, parser):
        parser.add_argument("--confirm-initial-setup",action="store_true")
    @transaction.atomic
    def handle(self,*args,**options):
        if not options["confirm_initial_setup"]:
            raise CommandError("This command requires --confirm-initial-setup.")
        if User.objects.filter(username__iexact="ADMIN").exists():
            raise CommandError("An ADMIN account already exists. No password or privileges were changed.")
        password = os.environ.get("KOFAD_INITIAL_ADMIN_PASSWORD")
        if not password:
            raise CommandError("Set KOFAD_INITIAL_ADMIN_PASSWORD for this one-time command.")
        call_command("bootstrap")
        user = User.objects.create(username="ADMIN",password=make_password(password),is_active=True,is_staff=True,is_superuser=True)
        Access.objects.filter(user=user).update(must_change_password=False, force_password_change=True)
        user.access.branches.set(Branch.objects.filter(active=True))
        audit(user,None,"admin.initialized",user.pk,{"must_change_password":False,"force_password_change":True})
        self.stdout.write(self.style.SUCCESS("ADMIN created with a temporary credential. A password change is required at first sign-in."))
