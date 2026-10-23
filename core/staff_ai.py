"""Read-only, permission-scoped KOFAD staff intelligence for web and Telegram.

Private business records never leave KOFAD for a free LLM. Every question is
routed to existing verified database summaries after current role/branch checks.
This module deliberately has no outbound message or write operations.
"""
import re

from .telegram_assistant import _staff_analysis, _staff_compare
from .staff_decisions import decision_brief, suggested_communication
from .telegram_intelligence import (
    _branches, _role, account_tasks, inventory_brief, sales_work_brief,
)


def _receivables_brief(user):
    """Live, allocation-aware balances for every finance-authorized branch.

    Match the Debt Desk's balance and overdue-grace rules. Collection allocations
    reduce invoice debt; a later approved correction reverses that allocation.
    """
    from datetime import timedelta
    from decimal import Decimal

    from django.db.models import F, Sum
    from django.utils import timezone

    from .debts import debt_policy
    from .models import Allocation, Document

    branches = _branches(user)
    if not _role(user)["management"] or not branches.exists():
        return "Your KOFAD role does not grant receivables reporting."

    today = timezone.localdate()
    invoices = list(Document.objects.filter(
        branch__in=branches, kind="sale", paid__lt=F("total"),
    ).order_by(F("due_date").asc(nulls_last=True), "created_at", "pk"))

    # Match core.services.balance() in bulk, avoiding a query per invoice.
    # Approved corrections reverse collections in the Debt Desk as well.
    applied = dict(
        Allocation.objects.filter(invoice__in=invoices)
        .exclude(payment_document__correction__status="approved")
        .values("invoice_id").annotate(applied_total=Sum("amount"))
        .values_list("invoice_id", "applied_total")
    )
    rows = [
        (invoice, invoice.total - invoice.paid - (applied.get(invoice.pk) or Decimal("0.00")))
        for invoice in invoices
    ]
    rows = [(invoice, amount) for invoice, amount in rows if amount > 0]
    grace_days = debt_policy().overdue_grace_days

    def total(items):
        return sum((amount for _, amount in items), Decimal("0.00"))

    overdue = [
        (invoice, amount) for invoice, amount in rows
        if invoice.due_date and today > invoice.due_date + timedelta(days=grace_days)
    ]
    due_soon = [
        (invoice, amount) for invoice, amount in rows
        if invoice.due_date and today <= invoice.due_date <= today + timedelta(days=7)
    ]
    posted_today = [
        (invoice, amount) for invoice, amount in rows
        if timezone.localdate(invoice.created_at) == today
    ]
    collected_today = Document.objects.filter(
        branch__in=branches, kind="collection", created_at__date=today,
    ).exclude(correction__status="approved").aggregate(
        amount=Sum("total"),
    )["amount"] or Decimal("0.00")
    top_due = [(invoice, amount) for invoice, amount in rows if invoice.due_date][:5]
    lines = [
        f"KOFAD CUSTOMER RECEIVABLES — {branches.count()} authorized location(s)",
        "Figures cover all authorized locations; the Debt Desk shows the selected branch.",
        f"Outstanding now: GHS {total(rows):,.2f} across {len(rows)} open invoice(s).",
        f"Overdue: GHS {total(overdue):,.2f} across {len(overdue)} invoice(s).",
        f"Due within seven days: GHS {total(due_soon):,.2f} across {len(due_soon)} invoice(s).",
        f"Posted today and still owing: GHS {total(posted_today):,.2f} across {len(posted_today)} invoice(s).",
        f"Debt collections posted today: GHS {collected_today:,.2f}.",
    ]
    if top_due:
        lines.append("Review these invoices first (earliest due):")
        for invoice, amount in top_due:
            lines.append(
                f"• {invoice.reference}: GHS {amount:,.2f} left; "
                f"due {invoice.due_date:%d %b %Y}."
            )
    else:
        lines.append("No dated open invoice currently needs scheduling.")
    lines.append(
        "Next: open Customer Debts, confirm the customer's payment evidence, "
        "then send only consented reminders or record verified collections. "
        "Today's figures are posted activity, not a historical opening-to-closing reconciliation."
    )
    return "\n".join(lines)


def _intent(text):
    q = " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())
    if re.search(r"\b(password|otp|verification code|payment pin|api key|secret)\b", q):
        return "security"
    if any(word in q for word in ("send", "message", "notify", "email", "gmail", "sms", "telegram", "draft")):
        return "communication"
    if any(word in q for word in ("why sales", "why revenue", "risk", "root cause",
                                  "deep analysis", "analyse", "analyze", "insight",
                                  "decision", "what should we", "forecast", "prediction",
                                  "recommend", "action plan")):
        return "insights"
    if any(word in q for word in ("my role", "my access", "my permission", "allowed to", "who am i")):
        return "role"
    if any(word in q for word in ("branch", "location", "where do i work")):
        return "branches"
    if any(word in q for word in ("receivable", "debt", "debtor", "invoice", "overdue", "arrears", "owing", "owed")):
        return "receivables"
    if any(word in q for word in ("low stock", "restock", "reorder", "inventory", "out of stock", "stock alert")):
        return "inventory"
    if any(word in q for word in ("pending order", "online order", "fulfil", "fulfill", "dispatch", "delivery work", "orders due")):
        return "orders"
    if any(word in q for word in ("compare", "trend", "growth", "increased", "decreased", "versus", " vs ")):
        return "compare"
    if any(word in q for word in ("sales", "revenue", "expense", "collection", "closing", "performance", "finance", "financial", "report", "profit")):
        return "reports"
    if any(word in q for word in ("priority", "priorities", "task", "urgent", "attention", "what next", "what should i do", "action")):
        return "tasks"
    if any(word in q for word in ("help", "abilities", "capabilities", "what can you do", "hello", "hi there", "good morning")):
        return "help"
    return "clarify"


def _role_branches_brief(user, branches, role):
    """Explain permissions and branch assignments together, not as two unrelated replies."""
    abilities = []
    if role["management"]:
        abilities.append("verified closings, finance trends and evidence-based reporting")
    if role["inventory"]:
        abilities.append("inventory exceptions and restocking reviews")
    if role["sales"]:
        abilities.append("online-order fulfilment reviews")
    names = list(branches.order_by("name").values_list("name", flat=True)[:30])
    return (
        "YOUR CURRENT KOFAD ACCESS\n"
        + "Authorized branches: " + ", ".join(names)
        + (" (first 30 shown)" if branches.count() > 30 else "")
        + "\nAvailable abilities: " + "; ".join(abilities)
        + "\nThese permissions are verified on each request. You can open the relevant workflows below."
    )


def _evidence_analysis(user, question, role):
    """Compose live, independently permission-scoped tools; no invented causal claims."""
    q = question.casefold()
    analysis = []
    if role["management"] and any(
        term in q for term in ("sale", "revenue", "trend", "performance", "clos", "why", "forecast")
    ):
        days = 30 if any(term in q for term in ("month", "monthly", "30 day")) else 7
        analysis.append("VERIFIED SALES COMPARISON\n" + _staff_compare(user, days))
    if role["management"] and any(
        term in q for term in ("debt", "receivab", "invoice", "overdue", "collection")
    ):
        analysis.append("RECEIVABLES EVIDENCE\n" + _receivables_brief(user))
    if role["inventory"] and any(
        term in q for term in ("inventory", "stock", "reorder", "restock", "shortage")
    ):
        analysis.append("STOCK EVIDENCE\n" + inventory_brief(user))
    if role["sales"] and any(
        term in q for term in ("order", "fulfil", "fulfill", "delivery")
    ):
        analysis.append("ORDER EVIDENCE\n" + sales_work_brief(user))
    if not analysis:
        return decision_brief(user)
    return (
        "\n\n".join(analysis[:3])
        + "\n\nWHAT TO CHECK NEXT\n"
        + decision_brief(user)
        + "\n\nInterpretation: these are recorded signals, not proof of why a trend occurred. "
          "Compare missing closings, product availability, timing and order status before acting."
    )


def staff_actions(user, source):
    """Explicit navigation tools. Never let client text choose URLs or bypass permissions."""
    if not user or not user.is_authenticated or not user.is_active:
        return []
    branches = _branches(user)
    if not branches.exists():
        return []
    roles = _role(user)
    if not any(roles.values()):
        return []
    actions = []
    if source in {"insights", "tasks", "compare", "reports"} and roles["management"]:
        if user.is_superuser or user.has_perm("core.view_reports") or user.has_perm("core.manage_company"):
            actions.append({"label": "Open reports", "href": "/reports/"})
        if user.is_superuser or user.has_perm("core.operate_finance"):
            actions.append({"label": "Review closings", "href": "/closings/"})
    if source in {"inventory", "insights", "tasks"} and roles["inventory"]:
        actions.append({"label": "Open inventory", "href": "/inventory/"})
    if source in {"orders", "insights", "tasks"} and roles["sales"]:
        actions.append({"label": "View online orders", "href": "/online-orders/"})
    if source == "receivables" and roles["management"] and (
        user.is_superuser or user.has_perm("core.operate_finance") or user.has_perm("core.manage_company")
    ):
        actions.append({"label": "Open customer debts", "href": "/debts/"})
    if source == "draft" and (user.is_superuser or user.has_perm("core.operate_sales")):
        actions.append({"label": "Review communications", "href": "/communications/"})
    if source in {"branches", "role"}:
        actions.append({"label": "My account", "href": "/account/"})
    return actions[:3]


def _honest_closing_report(report):
    """No verified closing must never be presented as zero actual business sales."""
    if "Verified closings reviewed: 0" not in report:
        return report
    headline = "\n".join(report.splitlines()[:3])
    return (
        headline + "\nNo verified closing records are available for this period. "
        "Sales, expense and collection totals are NOT established from closing data; "
        "this does not mean KOFAD made zero sales. "
        "Check /closings/ for missing submissions and review authorized live transaction "
        "reports before making a decision."
    )


def staff_reply(user, question, *, context=""):
    """Derive an answer from *current* permissions and authorized branches.

    No model/embeddings provider, customer PII, message send, or generated SQL.
    """
    if not user or not user.is_authenticated or not user.is_active:
        return "Sign in to KOFAD staff to use your private work assistant.", "denied"
    if not isinstance(question, str) or not 2 <= len(question.strip()) <= 600:
        return "Please ask a question between 2 and 600 characters.", "invalid"
    branches = _branches(user)
    if not branches.exists():
        return "Your account has no active authorized branch, or a required staff security step is incomplete.", "denied"
    role = _role(user)
    if not any(role.values()):
        return "Your staff role does not grant operational reporting access.", "denied"

    intent = _intent(question)
    q = question.casefold()
    # A short follow-up may reuse only the prior *category*, never data or
    # authority supplied by the browser. Every tool checks current roles.
    if (intent == "clarify" and context in {"reports", "compare", "inventory", "orders", "receivables", "insights"}
            and re.search(r"(?i)\b(?:what about|how about|same for|previous|last|this)\b", q)):
        intent = context
    if intent == "security":
        return ("Never share passwords, verification codes, PINs or API keys here. "
                "Use KOFAD's official security and account-recovery pages."), "security"
    if intent == "communication":
        return (
            "I cannot send unsolicited messages or contact customers from this chat.\n\n"
            + suggested_communication(user)
            + "\n\nFor SMS, Telegram or email, use the approved Communications Center. "
              "Check recipient consent, evidence and any delivery cost before sending."
        ), "draft"
    if intent == "insights":
        return _evidence_analysis(user, question, role), "insights"
    if intent == "role":
        allowed = []
        if role["management"]:
            allowed.append("verified closing reports and comparisons")
        if role["inventory"]:
            allowed.append("inventory exceptions")
        if role["sales"]:
            allowed.append("online-order fulfillment status")
        return (
            "Your KOFAD access includes " + ", ".join(allowed) + ".\n"
            "Authorized active branches: " + str(branches.count()) + ".\n"
            "All results are read-only, scoped to your current account and subject to live permission checks."
        ), "role"
    if intent == "branches" and any(token in q for token in ("abilit", "permission", "access", "assigned to me", "my role")):
        return _role_branches_brief(user, branches, role), "role"
    if intent == "branches":
        return (
            "Your authorized active KOFAD branches:\n"
            + "\n".join("• " + name for name in branches.order_by("name").values_list("name", flat=True)[:30])
            + ("\nShowing the first 30." if branches.count() > 30 else "")
        ), "branches"
    if intent == "receivables":
        if not role["management"]:
            return "Your current KOFAD role does not grant receivables reporting.", "denied"
        return _receivables_brief(user), "receivables"
    if intent == "inventory":
        if not role["inventory"]:
            return "Your current KOFAD role does not grant inventory reporting.", "denied"
        return inventory_brief(user) + "\nNext step: review /inventory/ before ordering or editing stock.", "inventory"
    if intent == "orders":
        if not role["sales"]:
            return "Your current KOFAD role does not grant online-order operations.", "denied"
        return sales_work_brief(user) + "\nNext step: review /online-orders/ for the permitted work queue.", "orders"
    if intent == "compare":
        if not role["management"]:
            return "Your current KOFAD role does not grant verified closing comparisons.", "denied"
        days = 30 if any(token in q for token in ("month", "30 day", "monthly")) else 7
        return (_staff_compare(user, days) + "\n\nCheck missing closings and operational exceptions before drawing conclusions."), "compare"
    if intent == "reports":
        if not role["management"]:
            return "Your current KOFAD role does not grant verified closing reports.", "denied"
        if any(token in q for token in ("yesterday", "previous day")):
            return _honest_closing_report(_staff_analysis(user, 1, offset=1)), "reports"
        days = 30 if any(token in q for token in ("month", "30 day", "monthly")) else 1 if "today" in q else 7
        return _honest_closing_report(_staff_analysis(user, days)) + "\nFor detail and reconciliation, open /workspace/.", "reports"
    if intent == "tasks":
        return decision_brief(user), "tasks"
    choices = ["your assigned branches", "evidence-ranked priorities", "operational risks"]
    if role["management"]:
        choices.extend(["sales today", "sales trends this month", "why sales changed", "overdue invoices and receivables"])
    if role["inventory"]:
        choices.append("low stock and restocking")
    if role["sales"]:
        choices.append("pending online orders")
    return (
        "Hello! I'm your KOFAD Staff Assistant. I can check " + ", ".join(choices)
        + ".\n\nTry: “What needs attention today?” or ask for a report using "
        "ordinary language. Figures come from authorized KOFAD records, not AI guesses. "
        "This chat cannot approve, edit, or send transactions; it will label missing evidence."
    ), "help"
