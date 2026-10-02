from decimal import Decimal
from django.utils import timezone
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Case, When, F, Sum, DecimalField, ExpressionWrapper, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from .models import Allocation, Branch, Document, Line, Product, QuarantineItem, Stock
from .services import balance

REGISTER = [(k,k.title()) for k in ["reference","date","kind","party","total","paid","balance"]]
FAMILIES = {"register":"Transaction register", "sales":"Sales and gross profit", "inventory":"Inventory valuation",
            "aging":"Receivables aging", "losses":"Inventory losses", "branches":"Branch comparison"}


def limited(queryset, maximum):
    if queryset.count() > maximum:
        raise ValidationError("This report exceeds its export limit. Narrow the date range where applicable; bulk export is required for larger snapshots. No partial totals were produced.")
    return queryset


def build_report(branch, first, last, family="register"):
    docs = Document.objects.filter(branch=branch,created_at__date__gte=first,created_at__date__lte=last)
    if family == "sales":
        grouped = {}
        for line in limited(Line.objects.filter(document__in=docs,document__kind__in=["sale","return"]).select_related("document","product"), 100000):
            key = (line.product_id,line.mode)
            row = grouped.setdefault(key,{"sku":line.product.sku,"product":line.description,"mode":line.mode.replace("_"," "),
                "units":0,"revenue":Decimal(0),"cost":Decimal(0),"profit":Decimal(0)})
            sign = -1 if line.document.kind == "return" else 1
            row["units"] += line.quantity * line.factor * sign
            row["revenue"] += line.total * sign
            row["cost"] += line.unit_cost * line.quantity * line.factor * sign
            row["profit"] = row["revenue"] - row["cost"]
        columns = [("sku","SKU"),("product","Product"),("mode","Mode"),("units","Net base units"),("revenue","Net revenue"),("cost","Standard cost"),("profit","Gross profit")]
        return list(grouped.values()), columns
    if family == "inventory":
        balances = dict(Stock.objects.filter(branch=branch).values_list("product_id", "quantity"))
        held = dict(QuarantineItem.objects.filter(branch=branch, status="held").values("product_id").annotate(
            total=Sum("quantity")).values_list("product_id", "total"))
        product_ids = set(balances) | set(held)
        products = limited(Product.objects.filter(pk__in=product_ids).order_by("name"), 10000)
        rows = []
        for product in products:
            sellable = balances.get(product.pk, 0)
            quarantine = held.get(product.pk, 0)
            physical = sellable + quarantine
            rows.append({"sku":product.sku,"product":product.name,"units":sellable,
                         "quarantine":quarantine,"physical":physical,
                         "packs":sellable // product.pack_size,"loose":sellable % product.pack_size,
                         "cost":product.cost,"value":product.cost*sellable,
                         "quarantine_value":product.cost*quarantine})
        return rows,[("sku","SKU"),("product","Product"),("units","Sellable base units"),
                     ("quarantine","Quarantined units"),("physical","Total physical units"),
                     ("packs","Sellable full packs"),("loose","Sellable loose units"),
                     ("cost","Standard unit cost"),("value","Sellable stock value"),
                     ("quarantine_value","Quarantine value")]
    if family == "losses":
        rows = []
        for doc in limited(Document.objects.filter(
            branch=branch, kind="inventory_writeoff",
            created_at__date__gte=first, created_at__date__lte=last
        ).prefetch_related("lines").select_related("created_by"), 10000):
            line = doc.lines.first()
            rows.append({"reference":doc.reference,"date":doc.created_at.strftime("%Y-%m-%d %H:%M"),
                         "product":line.description if line else "","units":line.quantity if line else 0,
                         "cost":line.unit_cost if line else Decimal(0),"value":doc.total,
                         "staff":doc.created_by.username,"reason":doc.note})
        return rows,[("reference","Reference"),("date","Date"),("product","Product"),("units","Units"),
                     ("cost","Unit cost"),("value","Loss value"),("staff","Recorded by"),("reason","Reason")]
    if family == "aging":
        rows = []
        today = timezone.localdate()
        for doc in limited(Document.objects.filter(branch=branch,kind="sale",party__isnull=False).select_related("party"), 10000):
            amount = balance(doc)
            if amount <= 0:
                continue
            overdue = max(0,(today-doc.due_date).days) if doc.due_date else 0
            bucket = "Current" if overdue == 0 else "1-30" if overdue <=30 else "31-60" if overdue <=60 else "61-90" if overdue<=90 else "90+"
            rows.append({"reference":doc.reference,"customer":doc.party.name,"phone":doc.party.phone,"due":str(doc.due_date or ""),
                         "days":overdue,"bucket":bucket,"balance":amount})
        return rows,[("reference","Invoice"),("customer","Customer"),("phone","Phone"),("due","Due date"),("days","Days overdue"),("bucket","Aging bucket"),("balance","Outstanding")]
    rows = [{"reference":d.reference,"date":d.created_at.strftime("%Y-%m-%d %H:%M"),"kind":d.get_kind_display(),
             "party":d.party.name if d.party else "Walk-in","total":-d.total if d.kind in ("return","supplier_return","reversal") else d.total,
             "paid":d.paid,"balance":balance(d) if d.kind in ("sale","purchase") else Decimal(0)}
            for d in limited(docs.select_related("party"), 10000)]
    return rows,REGISTER


def branch_comparison(user, first, last):
    """Aggregate in PostgreSQL; never trust a branch list supplied by the browser."""
    if not user.is_active or not user.has_perm("core.view_reports"):
        raise PermissionDenied("Reporting permission is required.")
    branches = Branch.objects.filter(active=True).order_by("name", "pk")
    if not user.is_superuser:
        branches = branches.filter(access__user=user)
    branch_list = list(branches)
    ids = [branch.pk for branch in branch_list]
    amount_type = DecimalField(max_digits=30, decimal_places=2)
    zero = Value(Decimal("0"), output_field=amount_type)
    rows = {branch.pk: {"branch": branch.name, "sales": Decimal(0), "cost": Decimal(0),
        "profit": Decimal(0), "expenses": Decimal(0), "losses": Decimal(0), "stock": Decimal(0),
        "quarantine": Decimal(0), "receivables": Decimal(0), "payables": Decimal(0)} for branch in branch_list}
    period = Document.objects.filter(branch_id__in=ids, created_at__date__gte=first, created_at__date__lte=last)
    lines = Line.objects.filter(document__in=period, document__kind__in=["sale", "return"]).order_by()
    revenue = Case(When(document__kind="return", then=-F("total")), default=F("total"), output_field=amount_type)
    cost = ExpressionWrapper(F("unit_cost") * F("quantity") * F("factor"), output_field=amount_type)
    signed_cost = Case(When(document__kind="return", then=-cost), default=cost, output_field=amount_type)
    for item in lines.values("document__branch_id").annotate(sales=Sum(revenue), cost=Sum(signed_cost)):
        row = rows[item["document__branch_id"]]
        row["sales"], row["cost"] = item["sales"], item["cost"]
        row["profit"] = row["sales"] - row["cost"]
    expense = Case(When(kind="expense", then=F("total")),
        When(kind="reversal", original__kind="expense", then=-F("total")),
        default=zero, output_field=amount_type)
    for item in period.order_by().values("branch_id").annotate(expenses=Sum(expense)):
        rows[item["branch_id"]]["expenses"] = item["expenses"]
    for item in period.filter(kind="inventory_writeoff").order_by().values("branch_id").annotate(losses=Sum("total")):
        rows[item["branch_id"]]["losses"] = item["losses"]
    value = ExpressionWrapper(F("quantity") * F("product__cost"), output_field=amount_type)
    for item in Stock.objects.filter(branch_id__in=ids).values("branch_id").annotate(value=Sum(value)):
        rows[item["branch_id"]]["stock"] = item["value"]
    quarantine_value = ExpressionWrapper(F("quantity") * F("unit_cost"), output_field=amount_type)
    for item in QuarantineItem.objects.filter(branch_id__in=ids, status="held").values("branch_id").annotate(
        value=Sum(quarantine_value)
    ):
        rows[item["branch_id"]]["quarantine"] = item["value"]
    # Correlated allocations avoid multiplying invoice totals when an invoice has several settlements.
    allocated = Allocation.objects.filter(invoice_id=OuterRef("pk")).exclude(
        payment_document__correction__status="approved").order_by().values("invoice_id").annotate(
        amount=Sum("amount")).values("amount")
    invoices = Document.objects.filter(branch_id__in=ids, kind__in=["sale", "purchase"]).annotate(
        settled=Coalesce(Subquery(allocated, output_field=amount_type), zero)).annotate(
        outstanding=ExpressionWrapper(F("total") - F("paid") - F("settled"), output_field=amount_type))
    for item in invoices.filter(outstanding__gt=0).order_by().values("branch_id", "kind").annotate(amount=Sum("outstanding")):
        rows[item["branch_id"]]["receivables" if item["kind"] == "sale" else "payables"] = item["amount"]
    columns = [("branch", "Branch"), ("sales", "Period net sales"), ("cost", "Period standard cost"),
        ("profit", "Period gross profit"), ("expenses", "Period net expenses"),
        ("losses", "Period inventory losses"), ("stock", "Current sellable stock value"),
        ("quarantine", "Current quarantine value"), ("receivables", "Current receivables"),
        ("payables", "Current payables")]
    return list(rows.values()), columns
