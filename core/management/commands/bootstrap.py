import os
from django.conf import settings
from django.contrib.auth.models import Group, Permission, User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from core.models import Access, Branch, Company, Party, Product
from core.services import stock_move, audit

ROLE_PERMISSIONS = {
    "Owner": ["operate_sales", "operate_inventory", "operate_finance", "approve_operations", "view_reports", "manage_company", "send_messages",
              "add_product", "change_product", "add_party", "change_party"],
    "Manager": ["send_messages", "operate_sales", "operate_inventory", "operate_finance", "approve_operations", "view_reports",
                "add_product", "change_product", "add_party", "change_party"],
    "Cashier": ["operate_sales", "add_party"],
    "Storekeeper": ["operate_inventory"],
    "Accountant": ["operate_finance", "view_reports", "change_party", "add_party"],
    "Auditor": ["view_reports"],
}


class Command(BaseCommand):
    help = "Initialize company, location and role templates. Demo data is opt-in and DEBUG-only."
    def add_arguments(self, parser):
        parser.add_argument("--demo", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        if options["demo"] and not settings.DEBUG:
            raise CommandError("Demo data is forbidden outside DEBUG environments.")
        Company.objects.get_or_create(pk=1)
        branch, _ = Branch.objects.get_or_create(code="main", defaults={"name": "Main branch"})
        for name, codes in ROLE_PERMISSIONS.items():
            group, created = Group.objects.get_or_create(name=name)
            if created:
                group.permissions.set(Permission.objects.filter(content_type__app_label="core", codename__in=codes))
        if options["demo"]:
            password = os.environ.get("DEMO_PASSWORD")
            if not password or len(password) < 12:
                raise CommandError("Set DEMO_PASSWORD to at least 12 characters for the isolated demo.")
            user, created = User.objects.get_or_create(username="demo")
            if created:
                user.set_password(password)
                user.save()
                user.groups.add(Group.objects.get(name="Owner"))
                user.access.branches.add(branch)
            examples = [
                ("KFD-001", "Classic leather sandals", "Footwear", 12, "pair", "box", "65.00", "720.00", "55.00", "600.00", "38.00", 240),
                ("KFD-002", "Everyday cotton tee", "Apparel", 24, "piece", "carton", "45.00", "960.00", "38.00", "840.00", "25.00", 144),
                ("KFD-003", "Utility paint brush 3 inch", "Hardware", 12, "piece", "box", "18.00", "192.00", None, "156.00", "9.00", 96),
                ("KFD-004", "Stainless steel flask", "Homeware", 6, "piece", "carton", "85.00", "480.00", "72.00", "408.00", "45.00", 36),
                ("KFD-005", "Canvas work gloves", "Hardware", 12, "pair", "pack", "12.00", "132.00", None, "108.00", "6.00", 8),
                ("KFD-006", "Travel carryall", "Travel", 4, "piece", "box", "160.00", "600.00", "140.00", "520.00", "95.00", 24),
            ]
            for sku, name, category, size, unit, pack, ru, rp, wu, wp, cost, qty in examples:
                product, created = Product.objects.get_or_create(sku=sku, defaults={
                    "name":name, "category":category, "pack_size":size, "base_unit":unit, "pack_name":pack,
                    "retail_unit":ru, "retail_pack":rp, "wholesale_unit":wu, "wholesale_pack":wp, "cost":cost})
                if created:
                    stock_move(user, branch, product, qty, "DEMO-OPENING", "Explicit demo opening stock")
            Party.objects.get_or_create(branch=branch, kind="customer", name="Sample Trading Store",
                defaults={"phone":"DEMO-NOT-A-REAL-NUMBER", "credit_limit":"5000"})
            Party.objects.get_or_create(branch=branch, kind="supplier", name="Sample Supply Company",
                defaults={"phone":"DEMO-NOT-A-REAL-NUMBER"})
            audit(user, branch, "demo.initialized", "demo")
        self.stdout.write(self.style.SUCCESS("Company, branch and role templates are ready."))
