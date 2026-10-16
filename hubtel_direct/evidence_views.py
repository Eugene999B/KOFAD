"""Owner-only, exact-byte Hubtel response evidence downloads (never public)."""
import json

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from marketplace.hubtel_evidence import decrypt_exchange
from marketplace.models import HubtelEvidence


def _allowed(request):
    from core import services
    from core.views import branch_for
    branch = branch_for(request)
    services.permit(request.user, branch, "manage_company")
    return Q(attempt__isnull=True) | Q(attempt__order__branch=branch)


def _private(response):
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_GET
def diagnostics(request):
    permitted = _allowed(request)
    kind = request.GET.get("type", "")
    choices = dict(HubtelEvidence._meta.get_field("direction").choices)
    entries = HubtelEvidence.objects.filter(permitted)
    if kind in choices:
        entries = entries.filter(direction=kind)
    entries = entries.order_by("-created_at", "-pk")[:150]
    return _private(render(request, "hubtel_direct/evidence.html", {
        "title": "Hubtel response downloads",
        "entries": entries, "kind": kind, "choices": choices,
    }))


@login_required
@require_GET
def download(request, evidence_id):
    evidence = get_object_or_404(HubtelEvidence.objects.filter(_allowed(request)), pk=evidence_id)
    raw = decrypt_exchange(evidence)
    try:
        json.loads(raw)
        content_type, extension = "application/json", "json"
    except (ValueError, UnicodeDecodeError, TypeError):
        content_type, extension = "text/plain; charset=utf-8", "txt"
    response = HttpResponse(raw, content_type=content_type)
    response["Content-Disposition"] = (
        f'attachment; filename="hubtel-{evidence.direction}-{evidence.pk}.{extension}"'
    )
    return _private(response)
