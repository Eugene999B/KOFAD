from decimal import Decimal
from django.utils import timezone
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Case, When, F, Sum, DecimalField, ExpressionWrapper, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from .models import Allocation, Branch, Document, Line, Stock
from .services import balance

REGISTER = [(k,k.title()) for k in ["reference","date","kind","party","total","paid","balance"]]
FAMILIES = {"register":"Transaction register", "sales":"Sales and gross profit", "inventory":"Inventory valuation", "aging":"Receivables aging", "branches":"Branch comparison"}


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
        rows = [{"sku":s.product.sku,"product":s.product.name,"units":s.quantity,"packs":s.quantity // s.product.pack_size,
                 "loose":s.quantity % s.product.pack_size,"cost":s.product.cost,"value":s.product.cost*s.quantity}
                for s in limited(Stock.objects.filter(branch=branch).select_related("product").order_by("product__name"), 10000)]
        return rows,[("sku","SKU"),("product","Product"),("units","Base units"),("packs","Full packs"),("loose","Loose units"),("cost","Standard unit cost"),("value","Stock value")]
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
             "party":d.party.name if d.party else "Walk-in","total":-d.total if d.kind in ("return","reversal") else d.total,
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
        "profit": Decimal(0), "expenses": Decimal(0), "stock": Decimal(0),
        "receivables": Decimal(0), "payables": Decimal(0)} for branch in branch_list}
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
    value = ExpressionWrapper(F("quantity") * F("product__cost"), output_field=amount_type)
    for item in Stock.objects.filter(branch_id__in=ids).values("branch_id").annotate(value=Sum(value)):
        rows[item["branch_id"]]["stock"] = item["value"]
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
        ("profit", "Period gross profit"), ("expenses", "Period net expenses"), ("stock", "Current stock value"),
        ("receivables", "Current receivables"), ("payables", "Current payables")]
    return list(rows.values()), columns
