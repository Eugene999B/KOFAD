"""Read-only, permission-scoped KOFAD staff intelligence for web and Telegram.

Private business records never leave KOFAD for a free LLM. Every question is
routed to existing verified database summaries after current role/branch checks.
This module deliberately has no outbound message or write operations.
"""
import re

from .telegram_assistant import _staff_analysis, _staff_compare
from .telegram_intelligence import (
    _branches, _role, account_tasks, inventory_brief, sales_work_brief,
)


def _receivables_brief(user):
    """Live invoice-level debt position for finance-authorized branches only.

    Report current balances and separately identify today's posted activity.
    Do not infer an opening balance from mutable invoices or reveal other
    branches' customers.
    """
    from datetime import timedelta
    from decimal import Decimal

    from django.db.models import DecimalField, F, Sum
    from django.utils import timezone

    from .models import Document

    branches = _branches(user)
    if not _role(user)["management"] or not branches.exists():
        return "Your KOFAD role does not grant receivables reporting."

    today = timezone.localdate()
    open_sales = Document.objects.filter(
        branch__in=branches, kind="sale", paid__lt=F("total"),
    )
    money_field = DecimalField(max_digits=18, decimal_places=2)

    def outstanding(queryset):
        return queryset.aggregate(
            amount=Sum(F("total") - F("paid"), output_field=money_field),
        )["amount"] or Decimal("0.00")

    overdue = open_sales.filter(due_date__lt=today)
    due_soon = open_sales.filter(due_date__gte=today, due_date__lte=today + timedelta(days=7))
    posted_today = open_sales.filter(created_at__date=today)
    collected_today = Document.objects.filter(
        branch__in=branches, kind="collection", created_at__date=today,
    ).aggregate(amount=Sum("total"))["amount"] or Decimal("0.00")
    top_due = list(
        open_sales.filter(due_date__isnull=False)
        .order_by("due_date", "created_at")
        .values("reference", "due_date", "total", "paid")[:5]
    )
    lines = [
        "KOFAD CUSTOMER RECEIVABLES — authorized locations",
        f"Outstanding now: GHS {outstanding(open_sales):,.2f} across {open_sales.count()} open invoice(s).",
        f"Overdue: GHS {outstanding(overdue):,.2f} across {overdue.count()} invoice(s).",
        f"Due within seven days: GHS {outstanding(due_soon):,.2f} across {due_soon.count()} invoice(s).",
        f"Posted today and still owing: GHS {outstanding(posted_today):,.2f} across {posted_today.count()} invoice(s).",
        f"Debt collections posted today: GHS {collected_today:,.2f}.",
    ]
    if top_due:
        lines.append("Review these invoices first (earliest due):")
        for row in top_due:
            balance = row["total"] - row["paid"]
            lines.append(
                f"• {row['reference']}: GHS {balance:,.2f} left; "
                f"due {row['due_date']:%d %b %Y}."
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
    if (intent == "clarify" and context in {"reports", "compare", "inventory", "orders", "receivables"}
            and re.search(r"(?i)\b(?:what about|how about|same for|previous|last|this)\b", q)):
        intent = context
    if intent == "security":
        return ("Never share passwords, verification codes, PINs or API keys here. "
                "Use KOFAD's official security and account-recovery pages."), "security"
    if intent == "communication":
        return (
            "I can help identify operational priorities, but I cannot send unsolicited messages "
            "or contact customers from this chat. Start with your role-scoped priorities:\n\n"
            + account_tasks(user)
            + "\n\nFor SMS, Telegram or email, use the approved Communications Center. "
            "Check recipient consent, contents and the delivery cost before sending."
        ), "draft"
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
            return _staff_analysis(user, 1, offset=1), "reports"
        days = 30 if any(token in q for token in ("month", "30 day", "monthly")) else 1 if "today" in q else 7
        return _staff_analysis(user, days) + "\nFor detail and reconciliation, open /workspace/.", "reports"
    if intent == "tasks":
        return account_tasks(user), "tasks"
    choices = ["your assigned branches", "current priorities"]
    if role["management"]:
        choices.extend(["sales today", "sales trends this month", "overdue invoices and receivables"])
    if role["inventory"]:
        choices.append("low stock and restocking")
    if role["sales"]:
        choices.append("pending online orders")
    return (
        "Hello! I'm your KOFAD Staff Assistant. I can check " + ", ".join(choices)
        + ".\n\nTry: “What needs attention today?” or ask for a report using "
        "ordinary language. Figures come from authorized KOFAD records, not AI guesses. "
        "This chat cannot approve, edit, or send transactions."
    ), "help"
