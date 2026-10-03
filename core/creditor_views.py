import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import creditors as creditor_service
from . import services as s
from .context import shell
from .exports import export
from .models import Document, Party
from .views import protected, problem


@protected("operate_inventory|operate_finance|view_reports")
def supplier_search(request, branch):
    query = request.GET.get("q", "").strip()[:100]
    rows = Party.objects.filter(branch=branch, kind="supplier")
    if query:
        rows = rows.filter(
            Q(name__icontains=query) | Q(phone__icontains=query) |
            Q(email__icontains=query) | Q(address__icontains=query)
        )
    results = []
    for party in rows.order_by("name")[:20]:
        purchases = Document.objects.filter(branch=branch, party=party, kind="purchase")
        last_purchase = purchases.order_by("-created_at").first()
        results.append({
            "id": party.pk,
            "name": party.name,
            "phone": party.phone,
            "email": party.email,
            "address": party.address,
            "outstanding": str(s.party_debt(party) + sum(
                (s.balance(doc) for doc in Document.objects.filter(
                    branch=branch, party=party, kind="creditor_charge"
                )), Decimal("0")
            )),
            "purchase_count": purchases.count(),
            "last_purchase_at": last_purchase.created_at.isoformat() if last_purchase else "",
        })
    return JsonResponse({"suppliers": results})


@protected("operate_finance|view_reports")
def creditors(request, branch):
    if request.method == "POST":
        try:
            s.permit(request.user, branch, "operate_finance")
            action = request.POST.get("action")
            if action == "new_bill":
                doc = creditor_service.post_creditor_bill(
                    request.user, branch, request.POST.dict(), request.POST.get("key")
                )
                messages.success(request, f"Creditor bill {doc.reference} recorded.")
                return redirect(f"/creditors/?creditor={doc.party_id}")
            if action == "payment":
                doc = creditor_service.post_supplier_account_payment(
                    request.user, branch, request.POST.dict(), request.POST.get("key")
                )
                messages.success(request, f"Creditor payment {doc.reference} recorded and allocated.")
                return redirect(f"/creditors/?creditor={doc.party_id}&payment={doc.pk}")
            raise ValidationError("Choose a valid creditor action.")
        except (ValidationError, ValueError) as exc:
            messages.error(request, problem(exc))

    query = request.GET.get("q", "").strip()[:100]
    status = request.GET.get("status", "open")
    if status not in {"open", "overdue", "due_7", "current", "settled", "all"}:
        status = "open"
    include_settled = status in {"settled", "all"}
    overview = creditor_service.creditors_overview(branch, query, include_settled=include_settled)
    rows = overview["rows"]
    if status == "open":
        rows = [row for row in rows if row["outstanding"] > 0]
    elif status == "overdue":
        rows = [row for row in rows if row["overdue"] > 0]
    elif status == "due_7":
        rows = [row for row in rows if row["due_7_days"] > 0]
    elif status == "current":
        rows = [row for row in rows if row["outstanding"] > 0 and row["overdue"] == 0]
    elif status == "settled":
        rows = [row for row in rows if row["outstanding"] <= 0]

    selected = None
    selected_id = request.GET.get("creditor", "") or request.POST.get("party", "")
    if str(selected_id).isdigit():
        party = Party.objects.filter(pk=int(selected_id), branch=branch, kind="supplier").first()
        if party:
            selected = {"party": party, **creditor_service.supplier_account_snapshot(party)}
    if selected is None and rows:
        selected = rows[0]

    selected_invoice = request.GET.get("invoice", "").strip()
    payment_doc = None
    payment_id = request.GET.get("payment", "")
    if payment_id:
        payment_doc = Document.objects.filter(
            pk=payment_id, branch=branch, kind="supplier_payment"
        ).select_related("party").first()

    suppliers = Party.objects.filter(branch=branch, kind="supplier").order_by("name")[:500]
    return render(request, "creditors.html", {
        "title": "Creditors",
        "q": query,
        "status": status,
        "overview": overview,
        "rows": rows,
        "selected": selected,
        "suppliers": suppliers,
        "methods": s.active_payment_methods(),
        "categories": creditor_service.PAYABLE_CATEGORIES,
        "today": timezone.localdate(),
        "key": str(uuid.uuid4()),
        "payment_doc": payment_doc,
        "selected_invoice": selected_invoice,
    })


@protected("operate_finance|view_reports")
def creditors_export(request, branch, format):
    query = request.GET.get("q", "").strip()[:100]
    status = request.GET.get("status", "open")
    include_settled = status in {"settled", "all"}
    overview = creditor_service.creditors_overview(branch, query, include_settled=include_settled)
    rows = overview["rows"]
    if status == "overdue":
        rows = [row for row in rows if row["overdue"] > 0]
    elif status == "due_7":
        rows = [row for row in rows if row["due_7_days"] > 0]
    elif status == "current":
        rows = [row for row in rows if row["outstanding"] > 0 and row["overdue"] == 0]
    elif status == "settled":
        rows = [row for row in rows if row["outstanding"] <= 0]
    elif status == "open":
        rows = [row for row in rows if row["outstanding"] > 0]

    data = [{
        "creditor": row["party"].name,
        "phone": row["party"].phone,
        "email": row["party"].email,
        "outstanding": row["outstanding"],
        "overdue": row["overdue"],
        "due_7": row["due_7_days"],
        "open_bills": row["bill_count"],
        "next_due": row["next_due"],
        "max_days": row["maximum_days_overdue"],
        "total_billed": row["total_billed"],
        "total_paid": row["total_paid"],
    } for row in rows]
    columns = [
        ("creditor", "Creditor / supplier"), ("phone", "Phone"), ("email", "Email"),
        ("outstanding", "Outstanding"), ("overdue", "Overdue"), ("due_7", "Due next 7 days"),
        ("open_bills", "Open bills"), ("next_due", "Next due date"),
        ("max_days", "Max days overdue"), ("total_billed", "Total billed"),
        ("total_paid", "Total payments"),
    ]
    s.audit(request.user, branch, "creditors.exported", format, {
        "status": status, "query": query, "rows": len(data),
    })
    return export(
        data, format, f"Creditors & accounts payable · {branch.name}",
        shell(request)["company"], columns,
        filename="kofad-creditors", sheet_name="Creditors",
        metadata={
            "Location": branch.name,
            "Status": status.replace("_", " ").title(),
            "Search": query or "All",
            "Generated": timezone.localtime().strftime("%d %b %Y %H:%M"),
        },
        summary={
            "Total payables": overview["total_payables"],
            "Overdue": overview["overdue"],
            "Due next 7 days": overview["due_7_days"],
            "Creditors owing": overview["creditors_owing"],
        },
        notes=["Aging is based on supplier due dates. Direct creditor bills and unpaid inventory purchases are combined into one supplier account."],
    )


@protected("operate_finance|view_reports")
def creditor_statement_export(request, branch, pk, format):
    party = get_object_or_404(Party, pk=pk, branch=branch, kind="supplier")
    statement = creditor_service.creditor_statement_rows(party)
    snapshot = creditor_service.supplier_account_snapshot(party)
    rows = [{
        "date": row["date"],
        "reference": row["reference"],
        "supplier_reference": row["external_reference"],
        "type": row["type"],
        "description": row["description"],
        "charge": row["charge"],
        "payment": row["payment"],
        "balance": row["running"],
    } for row in statement]
    columns = [
        ("date", "Date"), ("reference", "KOFAD reference"),
        ("supplier_reference", "Supplier reference"), ("type", "Type"),
        ("description", "Description"), ("charge", "Charge"),
        ("payment", "Payment"), ("balance", "Running balance"),
    ]
    s.audit(request.user, branch, "creditor.statement_exported", party.pk, {"format": format})
    return export(
        rows, format, f"Creditor statement · {party.name}",
        shell(request)["company"], columns,
        filename=f"kofad-creditor-statement-{party.pk}",
        sheet_name="Creditor statement",
        metadata={
            "Creditor": party.name, "Phone": party.phone,
            "Email": party.email or "—", "Location": branch.name,
            "Generated": timezone.localtime().strftime("%d %b %Y %H:%M"),
        },
        summary={
            "Outstanding": snapshot["outstanding"], "Overdue": snapshot["overdue"],
            "Open bills": snapshot["bill_count"], "Total paid": snapshot["total_paid"],
        },
    )
