"""Authenticated, CSRF-protected staff assistant; read-only local data only."""
import json

from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .staff_ai import staff_reply, staff_actions
from .views import protected

STAFF_PERMISSIONS = "manage_company|view_reports|operate_sales|operate_inventory|operate_finance"


def _security_ready(user):
    from .models import Access
    return Access.objects.filter(
        user=user, must_change_password=False, force_password_change=False,
    ).exists()


@never_cache
@protected(STAFF_PERMISSIONS)
@require_GET
def assistant_page(request, branch):
    if not _security_ready(request.user):
        return JsonResponse({"error": "Complete your required staff security steps."}, status=403)
    return render(request, "staff_ai.html", {"title": "KOFAD Staff AI Assistant"})


@never_cache
@protected(STAFF_PERMISSIONS)
@require_POST
def assistant_chat(request, branch):
    if not _security_ready(request.user):
        return JsonResponse({"error": "Complete your required staff security steps."}, status=403)
    if request.content_type != "application/json" or len(request.body) > 2048:
        return JsonResponse({"error": "Unsupported request."}, status=400)
    try:
        data = json.loads(request.body)
    except (UnicodeDecodeError, ValueError):
        return JsonResponse({"error": "Invalid request."}, status=400)
    if not isinstance(data, dict) or not isinstance(data.get("message"), str):
        return JsonResponse({"error": "Enter a message."}, status=400)
    if not 2 <= len(data["message"].strip()) <= 600:
        return JsonResponse({"error": "Keep messages between 2 and 600 characters."}, status=400)

    # Abuse control, bounded to one account/day. Fail closed on cache failure.
    from django.utils import timezone
    key = f"kofad:staff-ai:{request.user.pk}:{timezone.localdate():%Y%m%d}"
    try:
        allowed = cache.add(key, 1, timeout=86400) or cache.incr(key) <= 80
    except (ValueError, TypeError, ConnectionError):
        allowed = False
    if not allowed:
        return JsonResponse({"error": "You've reached the daily assistant limit."}, status=429)
    from .staff_ai_conversation import TOPIC_LABELS, conversational_reply
    from .staff_ai_planner import plan_and_answer

    context = data.get("context")
    safe_context = context if isinstance(context, str) and context in TOPIC_LABELS else ""
    recent = data.get("recent_topics")
    if not safe_context and isinstance(recent, list):
        for candidate in reversed(recent[-5:]):
            if isinstance(candidate, str) and candidate in TOPIC_LABELS:
                safe_context = candidate
                break
    # Turn counts help vary greetings, never grant access to data or tools.
    turn_count = data.get("turn_count")
    turn_count = min(turn_count, 40) if type(turn_count) is int and turn_count >= 0 else 0

    previous = data.get("recent_questions")
    previous = [
        item.strip()[:120] for item in previous[-4:]
        if isinstance(item, str) and 2 <= len(item.strip()) <= 120
    ] if isinstance(previous, list) else []
    dialogue = conversational_reply(
        request.user, data["message"], context=safe_context,
        turn_count=turn_count, recent_questions=previous,
    )
    if dialogue is not None:
        reply, source = dialogue
        external_ai_used = False
    else:
        planned = plan_and_answer(request.user, data["message"], context=safe_context)
        if planned is not None:
            reply, source, external_ai_used = planned
        else:
            from .staff_ai_general import general_reply
            general = general_reply(data["message"])
            if general is not None:
                reply, source, external_ai_used = general
            else:
                reply, source = staff_reply(request.user, data["message"], context=safe_context)
                external_ai_used = False
    response = JsonResponse({
        "reply": reply, "source": source, "external_ai_used": external_ai_used,
        "actions": staff_actions(request.user, source),
        "read_only": True,
    })
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
