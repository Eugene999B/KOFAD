import random
import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core import counts as count_service
from core import debts as debt_service
from core import inventory_exceptions as ix
from core import services as s
from core.models import (
    Branch, Closing, Company, Document, HeldSale, Message, Party, Product, Stock,
)


CONFIRMATION = "LOAD KOFAD SAMPLE DATA"
SAMPLE_PREFIX = "SAMPLE-"

CATALOG = {
    "Filters": [
        ("Hydraulic Oil Filter", 12, "box", "piece", 95),
        ("Engine Oil Filter", 12, "box", "piece", 75),
        ("Fuel Filter Primary", 12, "box", "piece", 68),
        ("Fuel Filter Secondary", 12, "box", "piece", 72),
        ("Air Filter Outer", 6, "box", "piece", 185),
        ("Air Filter Inner", 6, "box", "piece", 125),
        ("Pilot Filter", 12, "box", "piece", 54),
        ("Hydraulic Return Filter", 6, "box", "piece", 210),
        ("Suction Strainer", 4, "box", "piece", 240),
        ("Water Separator", 12, "box", "piece", 88),
        ("Transmission Filter", 6, "box", "piece", 195),
        ("Cabin Air Filter", 12, "box", "piece", 48),
    ],
    "Hydraulic": [
        ("Main Pump Seal Kit", 1, "kit", "kit", 780),
        ("Control Valve Seal Kit", 1, "kit", "kit", 620),
        ("Boom Cylinder Seal Kit", 1, "kit", "kit", 540),
        ("Arm Cylinder Seal Kit", 1, "kit", "kit", 520),
        ("Bucket Cylinder Seal Kit", 1, "kit", "kit", 495),
        ("Travel Motor Seal Kit", 1, "kit", "kit", 690),
        ("Swing Motor Seal Kit", 1, "kit", "kit", 640),
        ("Hydraulic Hose 1/2 Inch", 10, "bundle", "hose", 115),
        ("Hydraulic Hose 3/4 Inch", 10, "bundle", "hose", 155),
        ("Hydraulic Hose 1 Inch", 10, "bundle", "hose", 210),
        ("Hydraulic Coupling", 20, "box", "piece", 42),
        ("Hydraulic Pressure Sensor", 4, "box", "piece", 330),
    ],
    "Undercarriage": [
        ("Track Shoe", 1, "piece", "piece", 420),
        ("Track Bolt and Nut", 24, "box", "set", 38),
        ("Track Roller", 1, "piece", "piece", 1150),
        ("Carrier Roller", 1, "piece", "piece", 980),
        ("Front Idler Assembly", 1, "piece", "piece", 3900),
        ("Drive Sprocket", 1, "piece", "piece", 2450),
        ("Track Link Assembly", 1, "piece", "piece", 12800),
        ("Track Adjuster Seal Kit", 1, "kit", "kit", 580),
        ("Recoil Spring Assembly", 1, "piece", "piece", 3250),
        ("Track Guard", 1, "piece", "piece", 760),
        ("Track Master Pin", 4, "box", "piece", 290),
        ("Track Grease Valve", 10, "box", "piece", 85),
    ],
    "Engine": [
        ("Piston Ring Set", 1, "set", "set", 1450),
        ("Cylinder Liner", 4, "set", "piece", 780),
        ("Main Bearing Set", 1, "set", "set", 1250),
        ("Con Rod Bearing Set", 1, "set", "set", 980),
        ("Cylinder Head Gasket Set", 1, "set", "set", 1100),
        ("Injector Nozzle", 4, "box", "piece", 950),
        ("Turbocharger Assembly", 1, "piece", "piece", 6850),
        ("Engine Water Pump", 1, "piece", "piece", 2200),
        ("Engine Oil Pump", 1, "piece", "piece", 2650),
        ("Fan Belt", 5, "bundle", "piece", 185),
        ("Engine Mount", 4, "box", "piece", 480),
        ("Starter Motor", 1, "piece", "piece", 3400),
    ],
    "Electrical": [
        ("Alternator", 1, "piece", "piece", 3250),
        ("Starter Relay", 4, "box", "piece", 260),
        ("Battery Relay", 4, "box", "piece", 310),
        ("Ignition Switch", 4, "box", "piece", 240),
        ("Monitor Temperature Sensor", 4, "box", "piece", 380),
        ("Speed Sensor", 4, "box", "piece", 420),
        ("Solenoid Valve", 4, "box", "piece", 690),
        ("Main Wiring Harness", 1, "piece", "piece", 2400),
        ("LED Work Lamp", 4, "box", "piece", 360),
        ("Excavator Horn", 4, "box", "piece", 190),
        ("Fuse Box Assembly", 1, "piece", "piece", 620),
        ("Pressure Switch", 4, "box", "piece", 295),
    ],
    "Bucket & Pins": [
        ("Bucket Tooth", 10, "box", "piece", 185),
        ("Bucket Tooth Pin", 20, "box", "piece", 28),
        ("Side Cutter", 2, "pair", "piece", 520),
        ("Bucket Pin", 2, "pair", "piece", 680),
        ("Bucket Bush", 4, "box", "piece", 240),
        ("H-Link Bush", 4, "box", "piece", 260),
        ("Boom Pin", 2, "pair", "piece", 850),
        ("Arm Pin", 2, "pair", "piece", 790),
        ("Grease Nipple", 50, "box", "piece", 12),
        ("Cutting Edge", 1, "piece", "piece", 1650),
        ("Ripper Tooth", 5, "box", "piece", 310),
        ("Quick Coupler Pin", 2, "pair", "piece", 720),
    ],
}

FIRST_NAMES = [
    "Kwame", "Ama", "Kofi", "Akua", "Yaw", "Adwoa", "Kojo", "Abena",
    "Kwabena", "Afia", "Kwaku", "Yaa",
]
LAST_NAMES = [
    "Mensah", "Owusu", "Boateng", "Asare", "Osei", "Agyeman", "Frimpong", "Antwi",
]
BUSINESSES = [
    "Aseda Construction", "Golden Rock Mining", "Adom Earthworks", "Unity Haulage",
    "Prime Quarry Services", "Victory Plant Hire", "Apex Civil Works", "Royal Site Services",
    "Mighty Roads Ltd", "Obuasi Contractors", "Tarkwa Plant Services", "Ashanti Earth Movers",
]
TOWNS = ["Kumasi", "Obuasi", "Tarkwa", "Dunkwa-On-Offin", "Bibiani", "Accra", "Takoradi", "Konongo"]

SUPPLIERS = [
    "Heavy Parts Ghana", "Prime Hydraulic Supplies", "West Africa Filters",
    "Golden Engine Parts", "Reliable Undercarriage", "Plant Electrical Hub",
    "Mining Wear Parts", "Industrial Hose Centre",
]


def q2(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def sample_datetime(day, hour, minute=0):
    zone = timezone.get_current_timezone()
    return timezone.make_aware(datetime.combine(day, time(hour, minute)), zone)


class Command(BaseCommand):
    help = "Populate KOFAD with clearly marked sample business data for owner review."

    def add_arguments(self, parser):
        parser.add_argument("--confirm", default="")
        parser.add_argument("--days", type=int, default=24)
        parser.add_argument("--sales-per-day", type=int, default=5)
        parser.add_argument("--product-limit", type=int, default=72)
        parser.add_argument("--customer-limit", type=int, default=72)

    def handle(self, *args, **options):
        if options["confirm"] != CONFIRMATION:
            raise CommandError(f'This command requires --confirm "{CONFIRMATION}"')
        if Product.objects.filter(sku__startswith=SAMPLE_PREFIX).exists():
            raise CommandError("KOFAD sample data already exists. Reset business data before loading it again.")

        branch = Branch.objects.filter(active=True).first()
        admin = User.objects.filter(is_active=True, is_superuser=True).order_by("date_joined", "pk").first()
        company = Company.objects.first()
        if not branch or not admin or not company:
            raise CommandError("KOFAD needs an active branch, active system administrator and company settings first.")

        days = max(3, min(options["days"], 45))
        sales_per_day = max(2, min(options["sales_per_day"], 10))
        product_limit = max(12, min(options["product_limit"], 72))
        customer_limit = max(12, min(options["customer_limit"], 96))
        rng = random.Random(20261002)

        original_policy = {
            "allow_credit_sales": company.allow_credit_sales,
            "max_credit_days": company.max_credit_days,
            "payment_cash": company.payment_cash,
            "payment_momo": company.payment_momo,
            "payment_bank": company.payment_bank,
            "payment_card": company.payment_card,
        }

        reviewer, created = User.objects.get_or_create(
            username="kofad_sample_reviewer",
            defaults={
                "first_name": "Sample Data",
                "last_name": "Reviewer",
                "email": "sample-reviewer@example.invalid",
                "is_active": True,
            },
        )
        if created:
            reviewer.set_unusable_password()
        reviewer.is_active = True
        reviewer.save()
        reviewer.user_permissions.add(*Permission.objects.filter(codename__in=[
            "operate_inventory", "operate_finance", "approve_operations", "view_reports",
        ]))
        reviewer.access.branches.add(branch)

        try:
            Company.objects.filter(pk=company.pk).update(
                allow_credit_sales=True,
                max_credit_days=max(company.max_credit_days, 90),
                payment_cash=True,
                payment_momo=True,
                payment_bank=True,
                payment_card=True,
            )
            company.refresh_from_db()

            with transaction.atomic():
                products = self._create_products(admin, branch, product_limit, rng)
                customers = self._create_customers(branch, customer_limit)
                suppliers = self._create_suppliers(branch)
                s.audit(admin, branch, "showcase.catalog_created", "SAMPLE-DATA", {
                    "products": len(products), "customers": len(customers), "suppliers": len(suppliers),
                })

            today = timezone.localdate()
            business_days = []
            cursor = today - timedelta(days=days + 12)
            while cursor < today and len(business_days) < days:
                if cursor.weekday() != 6:
                    business_days.append(cursor)
                cursor += timedelta(days=1)
            if len(business_days) < days:
                raise CommandError("Could not build the requested historical business-day range.")

            sales = []
            purchases = []
            expenses = []
            for index, day in enumerate(business_days):
                with patch("django.utils.timezone.now", return_value=sample_datetime(day, 9)):
                    if index % 4 == 0:
                        purchases.extend(self._post_purchases(
                            admin, branch, products, suppliers, rng, count=2
                        ))

                for sale_no in range(sales_per_day):
                    hour = 10 + min(sale_no, 6)
                    with patch("django.utils.timezone.now", return_value=sample_datetime(day, hour, (sale_no * 7) % 55)):
                        sale = self._post_sale(
                            admin, branch, products, customers, rng,
                            sale_index=index * sales_per_day + sale_no,
                        )
                        sales.append(sale)

                if index % 2 == 0:
                    with patch("django.utils.timezone.now", return_value=sample_datetime(day, 15, 15)):
                        expenses.append(s.post_expense(admin, branch, {
                            "amount": str(q2(45 + (index % 6) * 35)),
                            "method": ["cash", "momo", "bank"][index % 3],
                            "note": [
                                "Sample local delivery and loading expense",
                                "Sample shop utilities and cleaning expense",
                                "Sample vehicle fuel and errands expense",
                            ][index % 3],
                        }, uuid.uuid4()))

                if index >= 4 and index % 3 == 0:
                    with patch("django.utils.timezone.now", return_value=sample_datetime(day, 16)):
                        self._collect_sample_debt(admin, branch, customers, index)

                if index >= 5 and index % 6 == 0 and purchases:
                    with patch("django.utils.timezone.now", return_value=sample_datetime(day, 16, 20)):
                        self._pay_supplier_debt(admin, branch, purchases, index)

                if index >= 6 and index % 7 == 0 and sales:
                    with patch("django.utils.timezone.now", return_value=sample_datetime(day, 16, 35)):
                        self._sample_return(reviewer, branch, sales, index)

                if index % 8 == 3:
                    with patch("django.utils.timezone.now", return_value=sample_datetime(day, 16, 45)):
                        product = products[(index * 3) % len(products)]
                        op = s.request_operation(admin, branch, {
                            "kind": "adjustment",
                            "product": product.pk,
                            "quantity": 2,
                            "reason": "Sample approved stock reconciliation gain",
                        })
                        s.advance_operation(reviewer, op.pk, "approve")

                if index == len(business_days) - 1:
                    with patch("django.utils.timezone.now", return_value=sample_datetime(day, 17)):
                        self._populate_exception_workflows(
                            admin, reviewer, branch, products, purchases, expenses
                        )
                        self._populate_stock_counts(admin, reviewer, branch, products)

                with patch("django.utils.timezone.now", return_value=sample_datetime(day, 18)):
                    net = s.channel_totals(branch, day)
                    opening_cash = Decimal("500.00")
                    counted = {method: str(value) for method, value in net.items()}
                    counted["cash"] = str(net["cash"] + opening_cash)
                    closing = s.submit_closing(
                        admin, branch, day, counted,
                        "Sample closing matched counted cash and provider evidence",
                        opening_cash=str(opening_cash),
                        cash_in="0",
                        cash_out="0",
                    )
                    s.verify_closing(reviewer, closing)

            self._populate_current_workspace(admin, branch, products, customers)

            s.audit(admin, branch, "showcase.seed.completed", "SAMPLE-DATA", {
                "products": Product.objects.filter(sku__startswith=SAMPLE_PREFIX).count(),
                "customers": Party.objects.filter(branch=branch, kind="customer", name__startswith="SAMPLE ·").count(),
                "suppliers": Party.objects.filter(branch=branch, kind="supplier", name__startswith="SAMPLE ·").count(),
                "sales": Document.objects.filter(branch=branch, kind="sale", note__icontains="sample").count(),
                "closings": Closing.objects.filter(branch=branch, date__in=business_days).count(),
                "warning": "Owner-requested sample/training data; not real business activity.",
            })

        finally:
            Company.objects.filter(pk=company.pk).update(**original_policy)
            reviewer.is_active = False
            reviewer.save(update_fields=["is_active"])

        self.stdout.write(self.style.SUCCESS(
            "KOFAD sample data loaded successfully. All generated contacts and catalog SKUs are clearly marked SAMPLE."
        ))

    def _create_products(self, admin, branch, limit, rng):
        products = []
        sequence = 1
        for category, rows in CATALOG.items():
            for name, pack_size, pack_name, base_unit, cost in rows:
                if len(products) >= limit:
                    return products
                cost = Decimal(str(cost))
                retail_unit = q2(cost * Decimal("1.45"))
                wholesale_unit = q2(cost * Decimal("1.27"))
                retail_pack = q2(retail_unit * pack_size * Decimal("0.96"))
                wholesale_pack = q2(wholesale_unit * pack_size * Decimal("0.97"))
                sku = f"{SAMPLE_PREFIX}{sequence:03d}"
                product = Product.objects.create(
                    name=f"SAMPLE · {name}",
                    sku=sku,
                    barcode=f"9902026{sequence:05d}",
                    category=category,
                    base_unit=base_unit,
                    pack_name=pack_name,
                    pack_size=pack_size,
                    cost=cost,
                    retail_unit=retail_unit,
                    retail_pack=retail_pack,
                    wholesale_unit=wholesale_unit,
                    wholesale_pack=wholesale_pack,
                    reorder_level=max(pack_size * 3, 6),
                    active=True,
                )
                opening = (
                    pack_size * rng.randint(28, 55) + rng.randrange(pack_size)
                    if pack_size > 1 else rng.randint(18, 48)
                )
                s.stock_move(
                    admin, branch, product, opening, f"SAMPLE-OPEN-{sequence:03d}",
                    "Sample opening stock for owner review",
                )
                products.append(product)
                sequence += 1
        return products

    def _create_customers(self, branch, limit):
        customers = []
        combinations = [(first, last) for first in FIRST_NAMES for last in LAST_NAMES]
        for index, (first, last) in enumerate(combinations[:limit], start=1):
            business = BUSINESSES[(index - 1) % len(BUSINESSES)] if index % 4 == 0 else ""
            label = f"{first} {last}" + (f" · {business}" if business else "")
            customers.append(Party.objects.create(
                branch=branch,
                kind="customer",
                name=f"SAMPLE · {label}",
                phone=f"+23399{index:07d}",
                email=f"sample-customer-{index:03d}@example.invalid",
                address=f"Sample address, {TOWNS[(index - 1) % len(TOWNS)]}, Ghana",
                credit_limit=Decimal("0") if index % 3 else Decimal("30000"),
                consent=index % 2 == 0,
            ))
        return customers

    def _create_suppliers(self, branch):
        return [
            Party.objects.create(
                branch=branch,
                kind="supplier",
                name=f"SAMPLE · {name}",
                phone=f"+23398{index:07d}",
                email=f"sample-supplier-{index:02d}@example.invalid",
                address=f"Sample supplier address, {TOWNS[index % len(TOWNS)]}, Ghana",
                credit_limit=0,
                consent=False,
            )
            for index, name in enumerate(SUPPLIERS, start=1)
        ]

    def _post_purchases(self, admin, branch, products, suppliers, rng, count=2):
        result = []
        for _ in range(count):
            product = rng.choice(products)
            supplier = rng.choice(suppliers)
            use_pack = product.pack_size > 1 and rng.random() < 0.7
            mode = "retail_pack" if use_pack else "retail_unit"
            quantity = rng.randint(3, 8) if use_pack else rng.randint(10, 35)
            unit_price = q2(product.cost * (product.pack_size if use_pack else 1) * Decimal("0.96"))
            total = q2(unit_price * quantity)
            paid = q2(total * Decimal("0.60")) if rng.random() < 0.45 else total
            payments = [{"method": "bank", "amount": str(paid)}] if paid else []
            due = timezone.localdate() + timedelta(days=30) if paid < total else None
            payload = {
                "party": supplier.pk,
                "items": [{"product": product.pk, "mode": mode, "quantity": quantity, "price": str(unit_price)}],
                "payments": payments,
                "note": "Owner-requested sample stock purchase",
            }
            if due:
                payload["due_date"] = due.isoformat()
            result.append(s.post_trade(admin, branch, payload, uuid.uuid4(), "purchase"))
        return result

    def _post_sale(self, admin, branch, products, customers, rng, sale_index):
        customer = customers[sale_index % len(customers)]
        line_count = 1 + (1 if sale_index % 4 == 0 else 0)
        chosen = rng.sample(products, k=min(line_count, len(products)))
        items = []
        total = Decimal("0")
        for product in chosen:
            wholesale = sale_index % 5 == 0
            use_pack = product.pack_size > 1 and sale_index % 3 == 0
            mode = ("wholesale_" if wholesale else "retail_") + ("pack" if use_pack else "unit")
            price = getattr(product, mode)
            quantity = 1 if use_pack else rng.randint(1, min(3, max(1, product.pack_size - 1)))
            items.append({"product": product.pk, "mode": mode, "quantity": quantity})
            total += q2(price * quantity)
        total = q2(total)

        arrangement = sale_index % 10
        if arrangement in (0, 1):
            payments = []
            due = timezone.localdate() + timedelta(days=14 + (sale_index % 10))
        elif arrangement in (2, 3, 4):
            paid = q2(total * Decimal("0.45"))
            method = ["cash", "momo", "bank"][sale_index % 3]
            payments = [{"method": method, "amount": str(paid)}]
            due = timezone.localdate() + timedelta(days=10 + (sale_index % 14))
        else:
            method = ["cash", "momo", "bank", "card"][sale_index % 4]
            payments = [{"method": method, "amount": str(total)}]
            due = None

        payload = {
            "party": customer.pk,
            "items": items,
            "payments": payments,
            "note": "Owner-requested sample sale for system review",
        }
        if due:
            payload["due_date"] = due.isoformat()
        return s.post_trade(admin, branch, payload, uuid.uuid4(), "sale")

    def _collect_sample_debt(self, admin, branch, customers, index):
        customer = customers[(index * 5) % len(customers)]
        snapshot = debt_service.customer_account_snapshot(customer)
        outstanding = snapshot["outstanding"]
        if outstanding <= 0:
            return
        amount = min(outstanding, q2(Decimal("250") + Decimal(index * 15)))
        debt_service.post_customer_payment(admin, branch, {
            "party": customer.pk,
            "amount": str(amount),
            "method": ["cash", "momo", "bank"][index % 3],
            "reference": f"SAMPLE-DEBT-{index:03d}",
        }, uuid.uuid4())

    def _pay_supplier_debt(self, admin, branch, purchases, index):
        candidates = [doc for doc in purchases if s.balance(doc) > 0]
        if not candidates:
            return
        invoice = candidates[index % len(candidates)]
        amount = min(s.balance(invoice), Decimal("850.00"))
        s.post_payment(admin, branch, {
            "invoice": str(invoice.pk),
            "amount": str(amount),
            "method": "bank",
            "reference": f"SAMPLE-SUP-{index:03d}",
        }, uuid.uuid4(), supplier=True)

    def _sample_return(self, reviewer, branch, sales, index):
        candidates = [
            doc for doc in sales
            if doc.lines.exists() and doc.lines.first().quantity > 0
        ]
        if not candidates:
            return
        sale = candidates[index % len(candidates)]
        line = sale.lines.first()
        returned = line.returns.aggregate_count() if hasattr(line, "returns") else 0
        if returned:
            return
        try:
            s.post_return(reviewer, branch, {
                "line": line.pk,
                "quantity": 1,
                "reason": "Sample customer return for owner review",
                "method": "cash",
            }, uuid.uuid4())
        except Exception:
            # Sample generation must not weaken real return rules; skip a line that is no longer eligible.
            return

    def _populate_exception_workflows(self, admin, reviewer, branch, products, purchases, expenses):
        product = products[0]
        held = ix.request_quarantine(
            admin, branch, product.pk, 2,
            "Sample quality inspection hold for owner review",
        )
        ix.review_quarantine(reviewer, branch, held.pk, True)

        resolved = ix.request_quarantine(
            admin, branch, products[1].pk, 1,
            "Sample damaged stock write-off workflow",
        )
        ix.review_quarantine(reviewer, branch, resolved.pk, True)
        ix.resolve_quarantine(
            reviewer, branch, resolved.pk, "writeoff",
            "Sample inspection confirmed permanent damage",
        )

        if purchases:
            source = purchases[0].lines.first()
            pending = ix.request_supplier_return(
                admin, branch, source.pk, 1,
                "Sample supplier-return request awaiting review", "bank",
            )
            # Keep one request pending so the queue is visible.
            _ = pending

            source2 = next((doc.lines.first() for doc in purchases[1:] if doc.lines.exists()), None)
            if source2:
                approved = ix.request_supplier_return(
                    admin, branch, source2.pk, 1,
                    "Sample approved supplier return", "bank",
                )
                ix.review_supplier_return(reviewer, branch, approved.pk, True)

        if expenses:
            correction = s.request_correction(
                admin, branch, expenses[-1].pk,
                "Sample expense correction awaiting independent review",
            )
            _ = correction

        op = s.request_operation(admin, branch, {
            "kind": "adjustment",
            "product": products[2].pk,
            "quantity": 1,
            "reason": "Sample adjustment request awaiting approval",
        })
        _ = op

    def _populate_stock_counts(self, admin, reviewer, branch, products):
        category = products[0].category
        count = count_service.start_count(admin, branch, uuid.uuid4(), category=category)
        values = {
            str(line.pk): (str(line.expected), "Sample blind count matched physical stock")
            for line in count.lines.all()
        }
        count_service.save_count(
            admin, branch, count.pk, values,
            note="Sample completed physical count", submit=True,
        )
        count_service.review_count(
            reviewer, branch, count.pk, "approve",
            "Sample count independently verified",
        )

        count_service.start_count(admin, branch, uuid.uuid4(), category=products[-1].category)

    def _populate_current_workspace(self, admin, branch, products, customers):
        for index in range(4):
            product = products[index]
            customer = customers[index]
            HeldSale.objects.create(
                branch=branch,
                user=admin,
                label=f"SAMPLE · Held sale {index + 1}",
                cart={
                    "items": [{
                        "product": product.pk,
                        "name": product.name,
                        "mode": "retail_unit",
                        "quantity": 1,
                        "factor": 1,
                        "price": str(product.retail_unit),
                        "listPrice": str(product.retail_unit),
                        "discount": "0",
                        "packName": product.pack_name,
                        "baseUnit": product.base_unit,
                    }],
                    "party": customer.pk,
                    "customer": {
                        "id": customer.pk,
                        "name": customer.name,
                        "phone": customer.phone,
                        "outstanding": str(s.party_debt(customer)),
                    },
                    "payment_plan": "full",
                    "due_date": "",
                },
            )

        for index, customer in enumerate(customers[:18], start=1):
            Message.objects.create(
                branch=branch,
                party=customer,
                channel="sms",
                body=(
                    f"SAMPLE MESSAGE: Hello {customer.name.replace('SAMPLE · ', '')}, "
                    "this is a training customer-account reminder from KOFAD."
                ),
                status="draft",
                created_by=admin,
                recipient=customer.phone,
                provider="arkesel",
                sender="KOFAD",
                sandbox=True,
                source_key=f"sample-message-{index:03d}",
            )
