"""Natural dialogue around read-only KOFAD tools.

The current browser provides only a previous *topic*, not private transcript
history. These responses never request a paid model or disclose staff records.
Every follow-up involving records re-enters the permission-checked staff tools.
"""
import re

# Only known response categories can establish continuation context.
TOPIC_LABELS = {
    "sales_activity": "sales",
    "sales_trend": "sales",
    "reports": "sales",
    "compare": "sales",
    "momo": "MoMo payments",
    "debt_activity": "customer debts",
    "receivables": "customer debts",
    "sms": "SMS activity",
    "email": "email activity",
    "inventory": "stock",
    "orders": "online orders",
    "diagnostics": "system health",
    "tasks": "priorities",
    "branches": "your assigned branches",
    "role": "your access",
    "insights": "business performance",
}
FOLLOW_UP_REPORTS = {
    "sales_activity": "sales",
    "reports": "sales",
    "compare": "sales",
    "momo": "momo",
    "debt_activity": "debts",
    "receivables": "debts",
    "sms": "sms",
    "email": "emails",
    "inventory": "stock",
    "orders": "online orders",
    "diagnostics": "system health",
}
GREETING = re.compile(r"^(hi|hello|hey|good morning|good afternoon|good evening|hello there|hi there)[!. ]*$")
FOLLOWUP_PERIOD = re.compile(
    r"^(?:(?:and|what about|how about|same for|for|show me|check|now|then)\s+)?"
    r"(yesterday|today|last week|this week|last month|this month|"
    r"(?:last|past|previous)\s+\d{1,3}\s+days?)\??$"
)


def conversational_reply(user, question, *, context=""):
    """Return (answer, source) or None to use the structured business router."""
    if not isinstance(question, str):
        return None
    value = " ".join(question.casefold().strip().split())
    normalized = value.strip(" \t\r\n.!?,")
    topic = TOPIC_LABELS.get(context)
    if GREETING.fullmatch(value):
        return ("Hi! What would you like to look into?", "conversation")
    if normalized in {"how are you", "how are you doing", "are you there"}:
        return ("I'm here and ready to help. What's on your mind?", "conversation")
    if normalized in {"thank you", "thanks", "thank you so much", "ok thanks", "okay thanks"}:
        return ("You're welcome!", "conversation")
    if normalized in {"who are you", "what are you", "what is this"}:
        return (
            "I'm KOFAD Assistant. I can help investigate authorized sales, "
            "MoMo, debt, stock, messages, orders and system records. "
            "I can also talk through what the figures mean.",
            "conversation",
        )
    if normalized in {"what can you do", "what can you help with", "help me"}:
        return (
            "I can check real KOFAD records, compare periods, spot discrepancies "
            "and explain what to investigate next. What's your question?",
            "conversation",
        )
    if normalized in {"what", "why", "how", "which", "huh", "what do you mean"}:
        if normalized == "why" and context in {"sales_activity", "sales_trend", "reports", "compare"}:
            from .staff_ai_sales import posted_sales_trend
            from .telegram_intelligence import _role
            if not _role(user)["management"]:
                return ("You don't have permission to see sales comparisons.", "denied")
            return (posted_sales_trend(user, "Why did sales change this week?"), "sales_trend")
        if normalized == "why" and topic:
            return (
                f"The {topic} figures show what KOFAD recorded, not necessarily why it happened. "
                "What change or result would you like me to investigate?",
                "clarify",
            )
        return (
            f"What would you like to know about {topic}?" if topic
            else "What would you like me to check?",
            "clarify",
        )
    if normalized in {"tell me more", "explain more", "explain that", "can you explain", "go deeper"}:
        if context in {"sales_activity", "sales_trend", "reports", "compare"}:
            from .staff_ai_sales import posted_sales_trend
            from .telegram_intelligence import _role
            if not _role(user)["management"]:
                return ("You don't have permission to see sales comparisons.", "denied")
            return (posted_sales_trend(user, "Compare sales this week"), "sales_trend")
        return (
            f"I can check a different period or dig into {topic}. What would be most useful?"
            if topic else "Which part would you like me to explore further?",
            "clarify",
        )
    # A bare date phrase continues ONLY the prior authorized topic, never user-supplied
    # data or permissions. The existing staff_reply validates everything again.
    match = FOLLOWUP_PERIOD.fullmatch(normalized)
    if match:
        label = FOLLOW_UP_REPORTS.get(context)
        if label:
            from .staff_ai import staff_reply
            period = match.group(1)
            return staff_reply(user, f"{label} {period}", context="")
        return ("Which report should I check for that period?", "clarify")
    if normalized in {"yes", "yes please", "sure", "okay", "ok", "no", "not really"}:
        if topic:
            return (
                f"Got it. What would you like me to check next about {topic}?",
                "clarify",
            )
        return ("Okay. What would you like to discuss?", "conversation")
    return None
