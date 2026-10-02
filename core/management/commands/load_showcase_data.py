import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core import services as s
from core.models import (
    Allocation, Audit, Branch, Closing, Company, Document, HeldSale, Line, Message,
    Movement, Operation, Party, Payment, Product, QuarantineItem, Stock, StockCount,
    StockCountLine, SupplierReturn,
)


MONEY = Decimal("0.01")
MARKER = "SHOWCASE-DATA-V1"


PRODUCT_NAMES = [
    ("Homeware", "Ceramic dinner plate set", "set", "carton", 6),
    ("Homeware", "Stainless steel flask 1L", "piece", "carton", 6),
    ("Homeware", "Non-stick frying pan 28cm", "piece", "carton", 8),
    ("Homeware", "Plastic storage bowl medium", "piece", "carton", 24),
    ("Homeware", "Glass tumbler set", "set", "carton", 12),
    ("Homeware", "Electric kettle 2L", "piece", "carton", 6),
    ("Cleaning", "Liquid dish wash 1L", "bottle", "carton", 12),
    ("Cleaning", "Laundry detergent 1kg", "pack", "carton", 12),
    ("Cleaning", "Floor cleaner 1L", "bottle", "carton", 12),
    ("Cleaning", "Multipurpose sponge pack", "pack", "carton", 24),
    ("Cleaning", "Heavy duty refuse bags", "roll", "carton", 20),
    ("Cleaning", "Microfiber cleaning cloth", "piece", "pack", 24),
    ("Personal Care", "Body lotion 400ml", "bottle", "carton", 12),
    ("Personal Care", "Bath soap 175g", "bar", "carton", 48),
    ("Personal Care", "Toothpaste family size", "tube", "carton", 24),
    ("Personal Care", "Shower gel 500ml", "bottle", "carton", 12),
    ("Personal Care", "Roll-on deodorant", "piece", "carton", 24),
    ("Personal Care", "Hair cream 250ml", "jar", "carton", 12),
    ("Stationery", "A4 exercise book 80 leaves", "book", "carton", 40),
    ("Stationery", "Blue ballpoint pen", "piece", "box", 50),
    ("Stationery", "Permanent marker black", "piece", "box", 12),
    ("Stationery", "A4 printing paper 80gsm", "ream", "box", 5),
    ("Stationery", "Office stapler medium", "piece", "box", 12),
    ("Stationery", "Brown envelope A4", "piece", "pack", 50),
    ("Hardware", "Utility paint brush 3 inch", "piece", "box", 12),
    ("Hardware", "Combination pliers 8 inch", "piece", "box", 12),
    ("Hardware", "Screwdriver set 6-piece", "set", "carton", 12),
    ("Hardware", "LED rechargeable torch", "piece", "carton", 12),
    ("Hardware", "Insulation tape black", "roll", "box", 20),
    ("Hardware", "Work gloves reinforced", "pair", "pack", 12),
    ("Travel", "Travel carryall medium", "piece", "box", 4),
    ("Travel", "Hard-shell cabin suitcase", "piece", "carton", 2),
    ("Travel", "School backpack large", "piece", "bale", 6),
    ("Travel", "Laptop backpack padded", "piece", "bale", 6),
    ("Travel", "Foldable shopping bag", "piece", "pack", 20),
    ("Travel", "Waist bag utility", "piece", "pack", 12),
    ("Apparel", "Everyday cotton T-shirt", "piece", "carton", 24),
    ("Apparel", "Polo shirt premium", "piece", "carton", 24),
    ("Apparel", "Men's boxer shorts", "piece", "pack", 12),
    ("Apparel", "Women's leggings", "piece", "pack", 12),
    ("Apparel", "Children's socks", "pair", "pack", 24),
    ("Apparel", "Baseball cap plain", "piece", "pack", 12),
    ("Footwear", "Classic leather sandals", "pair", "box", 12),
    ("Footwear", "Rubber bathroom slippers", "pair", "bale", 24),
    ("Footwear", "School shoes black", "pair", "carton", 12),
    ("Footwear", "Canvas sneakers", "pair", "carton", 12),
    ("Electronics", "USB-C fast charging cable", "piece", "box", 20),
    ("Electronics", "20W USB wall charger", "piece", "box", 20),
    ("Electronics", "Power bank 10000mAh", "piece", "carton", 10),
    ("Electronics", "Bluetooth mini speaker", "piece", "carton", 12),
    ("Electronics", "LED extension board", "piece", "carton", 12),
    ("Electronics", "Wireless computer mouse", "piece", "carton", 12),
]


CUSTOMER_NAMES = [
    "Ama Mensah", "Kwame Asare", "Akosua Boateng", "Kofi Owusu", "Adwoa Frimpong",
    "Yaw Osei", "Abena Serwaa", "Kojo Antwi", "Nana Yaa Appiah", "Kwaku Addo",
    "Afia Nyarko", "Daniel Agyeman", "Esi Amankwah", "Emmanuel Tetteh", "Priscilla Ansah",
    "Samuel Boadu", "Mavis Opoku", "Michael Darko", "Grace Adjei", "Joseph Amoako",
    "Linda Gyasi", "Richard Kwarteng", "Beatrice Danso", "Stephen Annan", "Rita Aidoo",
    "Felix Awuah", "Patricia Ofori", "Ernest Asiedu", "Diana Acheampong", "Charles Nkrumah",
    "Rosemary Donkor", "Francis Amponsah", "Theresa Baah", "Isaac Kyeremeh", "Victoria Manu",
    "Patrick Sarpong", "Janet Quaye", "George Arthur", "Mercy Akoto", "Collins Yeboah",
    "Bernice Dapaah", "Eric Poku", "Hannah Asante", "Martin Sakyi", "Gloria Nti",
    "Benjamin Fosu", "Regina Konadu", "David Boakye", "Esther Badu", "Peter Acheampong",
    "Showcase Adum Retail Hub", "Showcase Kejetia Mini Mart", "Showcase Asafo Trading",
    "Showcase Bantama Stores", "Showcase Suame Essentials", "Showcase Tafo Market Shop",
    "Showcase Ahodwo Household", "Showcase Oforikrom Ventures", "Showcase Ejisu Mart",
    "Showcase Asokwa Retail Point",
]


SUPPLIER_NAMES = [
    "Showcase Accra Import Warehouse",
    "Showcase Kumasi Distribution Centre",
    "Showcase Tema General Suppliers",
    "Showcase Ashanti Homeware Supply",
    "Showcase Prime Hardware Depot",
    "Showcase Everyday Consumer Goods",
    "Showcase Atlantic Trading House",
    "Showcase Golden Gate Wholesale",
    "Showcase Reliable Stationery Supply",
    "Showcase Urban Electronics Distribution",
]


def money(value):
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def stamp(day, hour, minute=0):
    value = datetime.combine(day, time(hour, minute))
    return timezone.make_aware(value, timezone.get_current_timezone())


def raw_insert(model, **fields):
    obj = model(**fields)
    obj.save_base(raw=True, force_insert=True, using="default")
    return obj


def showcase_ref(kind, number):
    return f"SHOW-{kind}-{number:04d}"


class Command(BaseCommand):
    help = "Populate KOFAD with deterministic synthetic showcase business data. Existing business records are preserved."

    def add_arguments(self, parser):
        parser.add_argument("--confirm-live-showcase", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG and not options["confirm_live_showcase"]:
            raise CommandError(
                "Production showcase loading requires --confirm-live-showcase. "
                "The command creates synthetic business transactions."
            )
        if Audit.objects.filter(action="showcase.seed.completed", reference=MARKER).exists():
            self.stdout.write(self.style.WARNING("Showcase data already exists; nothing was added."))
            return

        actor = User.objects.filter(is_active=True, is_superuser=True).order_by("pk").first()
        if not actor:
            raise CommandError("Create an active system administrator before loading showcase data.")
        branch = Branch.objects.filter(active=True).order_by("pk").first()
        if not branch:
            raise CommandError("Create an active business location before loading showcase data.")
        company = Company.objects.first()
        if not company:
            raise CommandError("Company settings are missing.")

        today = timezone.localdate()
        start_day = today - timedelta(days=35)
        balances = {}
        products = []

        # 52 products with substantial mixed pack/loose opening balances.
        for index, (category, name, unit, pack, pack_size) in enumerate(PRODUCT_NAMES, start=1):
            sku = f"SHOW-{index:03d}"
            base_cost = money(8 + index * Decimal("2.35"))
            retail_unit = money(base_cost * Decimal("1.58"))
            wholesale_unit = money(base_cost * Decimal("1.34"))
            retail_pack = money(retail_unit * pack_size * Decimal("0.94"))
            wholesale_pack = money(wholesale_unit * pack_size * Decimal("0.91"))
            product = Product.objects.create(
                name=f"{name}",
                sku=sku,
                barcode=f"880000{index:06d}",
                category=category,
                base_unit=unit,
                pack_name=pack,
                pack_size=pack_size,
                cost=base_cost,
                retail_unit=retail_unit,
                retail_pack=retail_pack,
                wholesale_unit=wholesale_unit,
                wholesale_pack=wholesale_pack,
                reorder_level=pack_size * 8,
                active=True,
            )
            opening = pack_size * (45 + (index % 20)) + (index % pack_size)
            balances[product.pk] = opening
            products.append(product)
            raw_insert(
                Movement,
                branch=branch,
                product=product,
                delta=opening,
                balance=opening,
                reference=f"SHOW-OPEN-{index:03d}",
                reason="Synthetic showcase opening stock",
                actor=actor,
                created_at=stamp(start_day, 8, index % 50),
            )

        customers = []
        for index, name in enumerate(CUSTOMER_NAMES, start=1):
            customer = Party.objects.create(
                branch=branch,
                kind="customer",
                name=f"Showcase · {name}" if not name.startswith("Showcase") else name,
                phone=f"+23320{1000000 + index:07d}",
                email=f"showcase.customer{index:02d}@example.test",
                address=["Kumasi", "Accra", "Ejisu", "Obuasi", "Mampong"][index % 5] + " · synthetic showcase address",
                credit_limit=money(2500 + (index % 8) * 1500),
                consent=False,
            )
            customers.append(customer)

        suppliers = []
        for index, name in enumerate(SUPPLIER_NAMES, start=1):
            suppliers.append(Party.objects.create(
                branch=branch,
                kind="supplier",
                name=name,
                phone=f"+23330{2000000 + index:07d}",
                email=f"showcase.supplier{index:02d}@example.test",
                address=["Accra", "Tema", "Kumasi", "Takoradi"][index % 4] + " · synthetic showcase address",
                credit_limit=0,
                consent=False,
            ))

        purchase_invoices = []
        purchase_lines = []
        purchase_outstanding = {}
        purchase_counter = 0

        # Restocking documents spread through the history.
        for index in range(24):
            day = start_day + timedelta(days=2 + index)
            supplier = suppliers[index % len(suppliers)]
            line_specs = []
            total = Decimal("0")
            for offset in range(3):
                product = products[(index * 5 + offset * 7) % len(products)]
                qty = 2 + ((index + offset) % 5)
                factor = product.pack_size
                unit_price = money(product.cost * factor * Decimal("0.93"))
                line_total = money(unit_price * qty)
                total += line_total
                line_specs.append((product, qty, factor, unit_price, line_total))
            total = money(total)
            if index % 4 == 0:
                paid = money(total * Decimal("0.45"))
            elif index % 7 == 0:
                paid = Decimal("0.00")
            else:
                paid = total
            purchase_counter += 1
            doc = raw_insert(
                Document,
                id=uuid.uuid4(),
                reference=showcase_ref("PUR", purchase_counter),
                branch=branch,
                kind="purchase",
                party=supplier,
                original=None,
                finalized=True,
                total=total,
                paid=paid,
                due_date=day + timedelta(days=21) if paid < total else None,
                note="Synthetic showcase purchase receipt",
                created_by=actor,
                created_at=stamp(day, 9, (index * 3) % 55),
            )
            purchase_invoices.append(doc)
            purchase_outstanding[doc.pk] = total - paid
            for product, qty, factor, unit_price, line_total in line_specs:
                line = Line.objects.create(
                    document=doc,
                    product=product,
                    description=product.name,
                    mode="retail_pack",
                    quantity=qty,
                    factor=factor,
                    list_price=unit_price,
                    unit_price=unit_price,
                    discount_percent=0,
                    unit_cost=product.cost,
                    total=line_total,
                )
                purchase_lines.append(line)
                delta = qty * factor
                balances[product.pk] += delta
                raw_insert(
                    Movement,
                    branch=branch,
                    product=product,
                    delta=delta,
                    balance=balances[product.pk],
                    reference=doc.reference,
                    reason="Synthetic showcase purchase receipt",
                    actor=actor,
                    created_at=stamp(day, 9, min(59, 5 + (index * 3) % 50)),
                )
            if paid:
                methods = ["bank", "momo", "cash"]
                raw_insert(
                    Payment,
                    document=doc,
                    method=methods[index % len(methods)],
                    amount=paid,
                    reference=f"SHOW-PUR-PAY-{index + 1:03d}",
                    direction=-1,
                )
            raw_insert(
                Audit,
                branch=branch,
                actor=actor,
                action="showcase.purchase_posted",
                reference=doc.reference,
                detail={"synthetic": True, "supplier": supplier.name, "total": str(total), "paid": str(paid)},
                created_at=stamp(day, 9, min(59, 10 + (index * 3) % 45)),
            )

        sales = []
        sale_outstanding = {}
        sale_counter = 0

        # 180 mixed retail/wholesale sales with full, partial and credit settlement.
        for index in range(180):
            day = start_day + timedelta(days=3 + (index % 31))
            customer = customers[(index * 7 + index // 5) % len(customers)]
            line_specs = []
            total = Decimal("0")
            line_count = 1 + (index % 4)
            chosen = set()
            for offset in range(line_count):
                product_index = (index * 11 + offset * 13) % len(products)
                while product_index in chosen:
                    product_index = (product_index + 1) % len(products)
                chosen.add(product_index)
                product = products[product_index]
                selector = (index + offset) % 4
                if selector == 0:
                    mode, factor, price = "retail_pack", product.pack_size, product.retail_pack
                    qty = 1 + ((index + offset) % 3)
                elif selector == 1:
                    mode, factor, price = "wholesale_pack", product.pack_size, product.wholesale_pack
                    qty = 1 + ((index + offset) % 4)
                elif selector == 2:
                    mode, factor, price = "retail_unit", 1, product.retail_unit
                    qty = 1 + ((index + offset * 2) % max(2, product.pack_size - 1))
                else:
                    mode, factor, price = "wholesale_unit", 1, product.wholesale_unit
                    qty = 2 + ((index + offset) % max(2, product.pack_size - 1))
                base_units = qty * factor
                if base_units > balances[product.pk]:
                    qty = 1
                    base_units = factor
                line_total = money(price * qty)
                total += line_total
                line_specs.append((product, mode, qty, factor, price, line_total))
            total = money(total)
            if index % 9 == 0:
                paid = Decimal("0.00")
            elif index % 6 == 0:
                paid = money(total * Decimal("0.40"))
            elif index % 11 == 0:
                paid = money(total * Decimal("0.70"))
            else:
                paid = total
            sale_counter += 1
            doc = raw_insert(
                Document,
                id=uuid.uuid4(),
                reference=showcase_ref("SALE", sale_counter),
                branch=branch,
                kind="sale",
                party=customer,
                original=None,
                finalized=True,
                total=total,
                paid=paid,
                due_date=day + timedelta(days=14 + (index % 12)) if paid < total else None,
                note="Synthetic showcase sale",
                created_by=actor,
                created_at=stamp(day, 10 + (index % 8), (index * 7) % 55),
            )
            sales.append(doc)
            sale_outstanding[doc.pk] = total - paid
            for product, mode, qty, factor, price, line_total in line_specs:
                Line.objects.create(
                    document=doc,
                    product=product,
                    description=product.name,
                    mode=mode,
                    quantity=qty,
                    factor=factor,
                    list_price=price,
                    unit_price=price,
                    discount_percent=0,
                    unit_cost=product.cost,
                    total=line_total,
                )
                delta = -(qty * factor)
                balances[product.pk] += delta
                if balances[product.pk] < 0:
                    raise CommandError(f"Showcase stock generation oversold {product.sku}.")
                raw_insert(
                    Movement,
                    branch=branch,
                    product=product,
                    delta=delta,
                    balance=balances[product.pk],
                    reference=doc.reference,
                    reason="Synthetic showcase sale",
                    actor=actor,
                    created_at=stamp(day, 10 + (index % 8), min(59, 1 + (index * 7) % 55)),
                )
            if paid:
                if index % 10 == 0 and paid >= Decimal("20.00"):
                    first = money(paid * Decimal("0.55"))
                    second = paid - first
                    raw_insert(Payment, document=doc, method="cash", amount=first, reference="", direction=1)
                    raw_insert(Payment, document=doc, method="momo", amount=second,
                               reference=f"SHOW-MOMO-{index + 1:04d}", direction=1)
                else:
                    method = ["cash", "momo", "bank", "card"][index % 4]
                    raw_insert(
                        Payment,
                        document=doc,
                        method=method,
                        amount=paid,
                        reference=f"SHOW-{method.upper()}-{index + 1:04d}" if method != "cash" else "",
                        direction=1,
                    )
            raw_insert(
                Audit,
                branch=branch,
                actor=actor,
                action="showcase.sale_posted",
                reference=doc.reference,
                detail={"synthetic": True, "customer": customer.name, "total": str(total), "paid": str(paid)},
                created_at=stamp(day, 10 + (index % 8), min(59, 2 + (index * 7) % 55)),
            )

        # Customer account payments. Several receipts deliberately remain open/overdue.
        debt_sales = [doc for doc in sales if sale_outstanding[doc.pk] > 0]
        collection_counter = 0
        for group_index in range(0, min(len(debt_sales), 36), 3):
            invoices = debt_sales[group_index:group_index + 3]
            available = sum((sale_outstanding[doc.pk] for doc in invoices), Decimal("0"))
            if available <= 0:
                continue
            amount = money(available * (Decimal("0.55") if group_index % 2 == 0 else Decimal("0.35")))
            if amount <= 0:
                continue
            latest_day = max(doc.created_at.date() for doc in invoices)
            day = min(today - timedelta(days=1), latest_day + timedelta(days=4))
            collection_counter += 1
            collection = raw_insert(
                Document,
                id=uuid.uuid4(),
                reference=showcase_ref("COL", collection_counter),
                branch=branch,
                kind="collection",
                party=invoices[0].party,
                original=invoices[0],
                finalized=True,
                total=amount,
                paid=amount,
                due_date=None,
                note="Synthetic showcase customer account payment · oldest due first",
                created_by=actor,
                created_at=stamp(day, 16, collection_counter % 50),
            )
            method = ["momo", "cash", "bank"][collection_counter % 3]
            raw_insert(
                Payment,
                document=collection,
                method=method,
                amount=amount,
                reference=f"SHOW-DEBT-{collection_counter:04d}" if method != "cash" else "",
                direction=1,
            )
            remaining = amount
            for invoice in sorted(invoices, key=lambda item: (item.due_date or item.created_at.date(), item.created_at)):
                if remaining <= 0:
                    break
                outstanding = sale_outstanding[invoice.pk]
                applied = min(outstanding, remaining)
                if applied > 0:
                    Allocation.objects.create(payment_document=collection, invoice=invoice, amount=applied)
                    sale_outstanding[invoice.pk] -= applied
                    remaining -= applied
            raw_insert(
                Audit,
                branch=branch,
                actor=actor,
                action="showcase.customer_debt_payment",
                reference=collection.reference,
                detail={"synthetic": True, "amount": str(amount), "method": method},
                created_at=stamp(day, 16, min(59, 5 + collection_counter % 50)),
            )

        # Supplier account payments across older open purchase invoices.
        supplier_payment_counter = 0
        open_purchases = [doc for doc in purchase_invoices if purchase_outstanding[doc.pk] > 0]
        for group_index in range(0, len(open_purchases), 2):
            invoices = open_purchases[group_index:group_index + 2]
            amount = money(sum((purchase_outstanding[doc.pk] for doc in invoices), Decimal("0")) * Decimal("0.50"))
            if amount <= 0:
                continue
            day = min(today - timedelta(days=1), max(doc.created_at.date() for doc in invoices) + timedelta(days=5))
            supplier_payment_counter += 1
            payment_doc = raw_insert(
                Document,
                id=uuid.uuid4(),
                reference=showcase_ref("SPAY", supplier_payment_counter),
                branch=branch,
                kind="supplier_payment",
                party=invoices[0].party,
                original=invoices[0],
                finalized=True,
                total=amount,
                paid=amount,
                due_date=None,
                note="Synthetic showcase supplier account payment",
                created_by=actor,
                created_at=stamp(day, 14, supplier_payment_counter % 45),
            )
            raw_insert(
                Payment,
                document=payment_doc,
                method="bank",
                amount=amount,
                reference=f"SHOW-SUP-BANK-{supplier_payment_counter:03d}",
                direction=-1,
            )
            remaining = amount
            for invoice in invoices:
                applied = min(purchase_outstanding[invoice.pk], remaining)
                if applied > 0:
                    Allocation.objects.create(payment_document=payment_doc, invoice=invoice, amount=applied)
                    purchase_outstanding[invoice.pk] -= applied
                    remaining -= applied

        # Customer returns against fully/mostly paid showcase sales.
        return_counter = 0
        for sale in sales[12:72:10]:
            source = sale.lines.order_by("pk").first()
            if not source:
                continue
            day = min(today - timedelta(days=1), sale.created_at.date() + timedelta(days=3))
            amount = money(source.unit_price)
            return_counter += 1
            ret = raw_insert(
                Document,
                id=uuid.uuid4(),
                reference=showcase_ref("RET", return_counter),
                branch=branch,
                kind="return",
                party=sale.party,
                original=sale,
                finalized=True,
                total=amount,
                paid=amount,
                due_date=None,
                note="Synthetic showcase customer return",
                created_by=actor,
                created_at=stamp(day, 15, return_counter * 3),
            )
            Line.objects.create(
                document=ret,
                product=source.product,
                description=source.description,
                mode=source.mode,
                quantity=1,
                factor=source.factor,
                list_price=source.list_price,
                unit_price=source.unit_price,
                discount_percent=source.discount_percent,
                unit_cost=source.unit_cost,
                total=amount,
                source_line=source,
            )
            raw_insert(Payment, document=ret, method="cash", amount=amount, reference="", direction=-1)
            delta = source.factor
            balances[source.product_id] += delta
            raw_insert(
                Movement,
                branch=branch,
                product=source.product,
                delta=delta,
                balance=balances[source.product_id],
                reference=ret.reference,
                reason="Synthetic showcase customer return",
                actor=actor,
                created_at=stamp(day, 15, min(59, return_counter * 3 + 1)),
            )

        # Approved supplier returns tied to original purchase lines.
        supplier_return_counter = 0
        for source in purchase_lines[5:30:6]:
            day = min(today - timedelta(days=1), source.document.created_at.date() + timedelta(days=6))
            qty = 1
            amount = money(source.unit_price * qty)
            supplier_return_counter += 1
            ret = raw_insert(
                Document,
                id=uuid.uuid4(),
                reference=showcase_ref("SRET", supplier_return_counter),
                branch=branch,
                kind="supplier_return",
                party=source.document.party,
                original=source.document,
                finalized=True,
                total=amount,
                paid=0,
                due_date=None,
                note="Synthetic showcase supplier return",
                created_by=actor,
                created_at=stamp(day, 11, supplier_return_counter * 5),
            )
            Line.objects.create(
                document=ret,
                product=source.product,
                description=source.description,
                mode=source.mode,
                quantity=qty,
                factor=source.factor,
                list_price=source.list_price,
                unit_price=source.unit_price,
                discount_percent=0,
                unit_cost=source.unit_cost,
                total=amount,
                source_line=source,
            )
            outstanding = purchase_outstanding.get(source.document_id, Decimal("0"))
            credit = min(outstanding, amount)
            if credit:
                Allocation.objects.create(payment_document=ret, invoice=source.document, amount=credit)
                purchase_outstanding[source.document_id] -= credit
            refund = amount - credit
            if refund > 0:
                ret.paid = refund
                # The document trigger allows update only while finalized is false, so create paid amount up-front is preferable.
                raise CommandError("Showcase supplier return generation produced an unexpected cash refund.")
            delta = -(qty * source.factor)
            balances[source.product_id] += delta
            raw_insert(
                Movement,
                branch=branch,
                product=source.product,
                delta=delta,
                balance=balances[source.product_id],
                reference=ret.reference,
                reason="Synthetic showcase supplier return",
                actor=actor,
                created_at=stamp(day, 11, min(59, supplier_return_counter * 5 + 1)),
            )
            raw_insert(
                SupplierReturn,
                id=uuid.uuid4(),
                branch=branch,
                source_line=source,
                quantity=qty,
                refund_method="bank",
                reason="Synthetic showcase supplier return for damaged or incorrect stock",
                status="approved",
                requested_by=actor,
                reviewed_by=actor,
                posted=ret,
                created_at=stamp(day - timedelta(days=1), 13, supplier_return_counter),
                reviewed_at=stamp(day, 10, supplier_return_counter),
            )

        # Expenses distributed across the month.
        expense_labels = [
            "Local delivery fuel", "Shop cleaning supplies", "Loading and offloading",
            "Internet and data", "Minor shop repairs", "Packaging materials",
            "Local transport", "Electricity contribution", "Printing and stationery",
            "Customer delivery support",
        ]
        expense_counter = 0
        for index in range(32):
            day = start_day + timedelta(days=2 + (index % 31))
            amount = money(35 + (index % 9) * 27.5)
            expense_counter += 1
            expense = raw_insert(
                Document,
                id=uuid.uuid4(),
                reference=showcase_ref("EXP", expense_counter),
                branch=branch,
                kind="expense",
                party=None,
                original=None,
                finalized=True,
                total=amount,
                paid=amount,
                due_date=None,
                note="Synthetic showcase · " + expense_labels[index % len(expense_labels)],
                created_by=actor,
                created_at=stamp(day, 13, (index * 9) % 55),
            )
            method = ["cash", "momo", "bank"][index % 3]
            raw_insert(
                Payment,
                document=expense,
                method=method,
                amount=amount,
                reference=f"SHOW-EXP-{index + 1:03d}" if method != "cash" else "",
                direction=-1,
            )
            raw_insert(
                Audit,
                branch=branch,
                actor=actor,
                action="showcase.expense_posted",
                reference=expense.reference,
                detail={"synthetic": True, "amount": str(amount), "method": method},
                created_at=stamp(day, 13, min(59, 1 + (index * 9) % 55)),
            )

        # Quarantine queue and inventory-loss evidence.
        for index, product in enumerate(products[3:18:4], start=1):
            qty = min(2 + index, max(1, balances[product.pk] // 20))
            balances[product.pk] -= qty
            raw_insert(
                Movement,
                branch=branch,
                product=product,
                delta=-qty,
                balance=balances[product.pk],
                reference=f"SHOW-QUAR-{index:03d}",
                reason="Synthetic showcase damaged-stock quarantine",
                actor=actor,
                created_at=stamp(today - timedelta(days=2 + index), 12, index),
            )
            raw_insert(
                QuarantineItem,
                id=uuid.uuid4(),
                branch=branch,
                product=product,
                quantity=qty,
                unit_cost=product.cost,
                reason="Synthetic showcase: packaging damaged during handling",
                status="held",
                requested_by=actor,
                reviewed_by=actor,
                resolved_by=None,
                resolution_note="",
                loss_document=None,
                created_at=stamp(today - timedelta(days=3 + index), 16, index),
                reviewed_at=stamp(today - timedelta(days=2 + index), 12, index),
                resolved_at=None,
            )

        for index, product in enumerate(products[22:30:3], start=1):
            qty = 1 + index
            balances[product.pk] -= qty
            day = today - timedelta(days=5 + index)
            raw_insert(
                Movement,
                branch=branch,
                product=product,
                delta=-qty,
                balance=balances[product.pk],
                reference=f"SHOW-LOSS-{index:03d}",
                reason="Synthetic showcase damaged stock written off",
                actor=actor,
                created_at=stamp(day, 12, index),
            )
            loss_total = money(product.cost * qty)
            loss_doc = raw_insert(
                Document,
                id=uuid.uuid4(),
                reference=showcase_ref("LOSS", index),
                branch=branch,
                kind="inventory_writeoff",
                party=None,
                original=None,
                finalized=True,
                total=loss_total,
                paid=0,
                due_date=None,
                note="Synthetic showcase inventory write-off",
                created_by=actor,
                created_at=stamp(day, 12, index + 2),
            )
            Line.objects.create(
                document=loss_doc,
                product=product,
                description=product.name,
                mode="writeoff",
                quantity=qty,
                factor=1,
                list_price=product.cost,
                unit_price=product.cost,
                discount_percent=0,
                unit_cost=product.cost,
                total=loss_total,
            )
            raw_insert(
                QuarantineItem,
                id=uuid.uuid4(),
                branch=branch,
                product=product,
                quantity=qty,
                unit_cost=product.cost,
                reason="Synthetic showcase: goods failed damage inspection",
                status="written_off",
                requested_by=actor,
                reviewed_by=actor,
                resolved_by=actor,
                resolution_note="Synthetic showcase write-off after inspection",
                loss_document=loss_doc,
                created_at=stamp(day - timedelta(days=2), 15, index),
                reviewed_at=stamp(day - timedelta(days=1), 10, index),
                resolved_at=stamp(day, 12, index + 3),
            )

        # Pending inventory operations, stock counts, held carts and communications.
        for index, product in enumerate(products[:8], start=1):
            raw_insert(
                Operation,
                id=uuid.uuid4(),
                branch=branch,
                destination=None,
                product=product,
                kind="adjustment",
                quantity=1 if index % 2 else -1,
                reason="Synthetic showcase stock adjustment awaiting independent review",
                status="requested",
                requested_by=actor,
                approved_by=None,
                created_at=stamp(today - timedelta(days=index % 3), 9, index),
            )

        for count_index in range(3):
            count = raw_insert(
                StockCount,
                id=uuid.uuid4(),
                branch=branch,
                scope=["Fast-moving products", "Homeware & cleaning", "Electronics & hardware"][count_index],
                status="draft",
                note="Synthetic showcase blind-count session",
                review_note="",
                created_by=actor,
                reviewed_by=None,
                created_at=stamp(today - timedelta(days=count_index), 8, 10 + count_index),
                submitted_at=None,
                reviewed_at=None,
            )
            for product in products[count_index * 8:(count_index + 1) * 8]:
                StockCountLine.objects.create(
                    count=count,
                    product=product,
                    expected=balances[product.pk],
                    movement_id=0,
                    counted=None,
                    reason="",
                )

        for index in range(6):
            product = products[(index * 7) % len(products)]
            HeldSale.objects.create(
                branch=branch,
                user=actor,
                label=f"Showcase held sale · {customers[index].name}",
                cart={
                    "items": [{
                        "product": product.pk,
                        "name": product.name,
                        "mode": "retail_pack" if product.pack_size > 1 else "retail_unit",
                        "quantity": 1,
                        "factor": product.pack_size,
                        "price": str(product.retail_pack or product.retail_unit),
                        "listPrice": str(product.retail_pack or product.retail_unit),
                        "discount": "0",
                    }],
                    "party": customers[index].pk,
                    "customer": {"id": customers[index].pk, "name": customers[index].name,
                                 "phone": customers[index].phone, "outstanding": "0.00"},
                },
            )

        for index in range(24):
            customer = customers[(index * 5) % len(customers)]
            raw_insert(
                Message,
                branch=branch,
                party=customer,
                channel="sms" if index % 3 else "whatsapp",
                body=(
                    f"Showcase message for {customer.name}: thank you for trading with "
                    f"{company.name}. This is synthetic demonstration content."
                ),
                status="draft",
                created_by=actor,
                recipient=customer.phone,
                provider="arkesel" if index % 3 else "",
                sender="",
                sandbox=True,
                segments=1,
                encoding="gsm7",
                attempts=0,
                next_attempt_at=None,
                queued_by=None,
                source_key=f"showcase-message-{index + 1:03d}",
                last_error="",
                created_at=stamp(today - timedelta(days=index % 12), 11, index % 55),
            )

        # Build stock balances after every synthetic movement has been recorded.
        for product in products:
            Stock.objects.create(branch=branch, product=product, quantity=balances[product.pk])

        # Daily closing snapshots for historical days only; today remains open for real testing.
        closing_count = 0
        for offset in range(14, 0, -1):
            day = today - timedelta(days=offset)
            if Closing.objects.filter(branch=branch, date=day).exists():
                continue
            summary = s.closing_summary(branch, day)
            opening_cash = money(500 + (offset % 4) * 100)
            cash_in = money(50 if offset % 5 == 0 else 0)
            cash_out = money(30 if offset % 6 == 0 else 0)
            expected = dict(summary["channel_net"])
            expected["cash"] = money(expected["cash"] + opening_cash + cash_in - cash_out)
            variance = Decimal("0.00")
            if offset % 7 == 0:
                variance = Decimal("5.00")
            counted = {method: money(value) for method, value in expected.items()}
            counted["cash"] = money(counted["cash"] + variance)
            variances = {method: counted[method] - expected[method] for method in counted}
            summary["cash_control"] = {
                "opening_cash": opening_cash,
                "other_cash_in": cash_in,
                "other_cash_out": cash_out,
                "expected_cash": expected["cash"],
            }
            summary["variance"] = variances
            raw_insert(
                Closing,
                branch=branch,
                date=day,
                expected={key: str(value) for key, value in expected.items()},
                counted={key: str(value) for key, value in counted.items()},
                opening_cash=opening_cash,
                cash_in=cash_in,
                cash_out=cash_out,
                summary=s._closing_json(summary),
                note="Synthetic showcase closing" + (" · GHS 5 cash variance explained" if variance else ""),
                submitted_by=actor,
                verified_by=None,
                created_at=stamp(day, 20, 30),
            )
            closing_count += 1

        # Pending supplier-return requests keep that workflow populated without changing stock twice.
        for index, source in enumerate(purchase_lines[2:32:7], start=1):
            raw_insert(
                SupplierReturn,
                id=uuid.uuid4(),
                branch=branch,
                source_line=source,
                quantity=1,
                refund_method="bank",
                reason="Synthetic showcase supplier return awaiting independent review",
                status="requested",
                requested_by=actor,
                reviewed_by=None,
                posted=None,
                created_at=stamp(today - timedelta(days=index), 10, index),
                reviewed_at=None,
            )

        raw_insert(
            Audit,
            branch=branch,
            actor=actor,
            action="showcase.seed.completed",
            reference=MARKER,
            detail={
                "synthetic": True,
                "products": len(products),
                "customers": len(customers),
                "suppliers": len(suppliers),
                "sales": len(sales),
                "purchases": len(purchase_invoices),
                "customer_debt_payments": collection_counter,
                "supplier_payments": supplier_payment_counter,
                "expenses": expense_counter,
                "historical_closings": closing_count,
                "note": "All SHOW-/Showcase records are synthetic demonstration data and may be removed with Reset business data.",
            },
            created_at=timezone.now(),
        )

        self.stdout.write(self.style.SUCCESS(
            f"Showcase data loaded: {len(products)} products, {len(customers)} customers, "
            f"{len(suppliers)} suppliers, {len(sales)} sales, {len(purchase_invoices)} purchases, "
            f"{collection_counter} customer debt payments, {expense_counter} expenses and "
            f"{closing_count} historical closings. Today remains open."
        ))
