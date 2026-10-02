from decimal import Decimal
from django.utils import timezone
from .models import Document, Line, Stock
from .services import balance

REGISTER = [(k,k.title()) for k in ["reference","date","kind","party","total","paid","balance"]]
FAMILIES = {"register":"Transaction register", "sales":"Sales and gross profit", "inventory":"Inventory valuation", "aging":"Receivables aging"}


def build_report(branch, first, last, family="register"):
    docs = Document.objects.filter(branch=branch,created_at__date__gte=first,created_at__date__lte=last)
    if family == "sales":
        grouped = {}
        for line in Line.objects.filter(document__in=docs,document__kind__in=["sale","return"]).select_related("document","product")[:100000]:
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
                for s in Stock.objects.filter(branch=branch).select_related("product").order_by("product__name")[:10000]]
        return rows,[("sku","SKU"),("product","Product"),("units","Base units"),("packs","Full packs"),("loose","Loose units"),("cost","Standard unit cost"),("value","Stock value")]
    if family == "aging":
        rows = []
        today = timezone.localdate()
        for doc in Document.objects.filter(branch=branch,kind="sale",party__isnull=False).select_related("party")[:10000]:
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
            for d in docs.select_related("party")[:10000]]
    return rows,REGISTER
