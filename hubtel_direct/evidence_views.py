"""Owner-only, exact-byte Hubtel response evidence downloads (never public)."""
import json
import io
import re
import zipfile
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

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


def _transaction_for_reference(request, reference):
    """Only previously saved original refs, never a caller-supplied arbitrary URL."""
    from marketplace.models import MarketPaymentAttempt
    from hubtel_direct.models import DirectTransaction, DebtMomoCollection
    from core.views import branch_for
    branch = branch_for(request)
    hosted = MarketPaymentAttempt.objects.filter(
        reference=reference, provider="hubtel", order__branch=branch,
    ).first()
    if hosted:
        return "hosted", hosted
    direct = DirectTransaction.objects.filter(client_reference=reference).first()
    if direct and (
        (direct.order_id and direct.order.branch_id == branch.pk)
        or (direct.requested_by_id == request.user.pk)
    ):
        return "direct", direct
    debt = DebtMomoCollection.objects.filter(reference=reference, branch=branch).first()
    if debt:
        return "debt_momo", debt
    from core import pos_hubtel
    try:
        pos = pos_hubtel.held_for(reference)
    except Exception:
        pos = None
    if pos and pos.branch_id == branch.pk:
        return "staff_pos", pos
    return "", None


def _reference_allowed(request, reference):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", reference or ""):
        return False
    return bool(_transaction_for_reference(request, reference)[0])


def _provider_status(record):
    """Label raw provider fields; do not invent an accounting/payment result."""
    if record.direction not in {"status_check", "direct_status", "callback"}:
        return ""
    try:
        payload = json.loads(decrypt_exchange(record))
        if not isinstance(payload, dict):
            return "Invalid provider JSON"
        data = payload.get("Data", payload.get("data"))
        if isinstance(data, list):
            data = next((
                item for item in data
                if isinstance(item, dict)
                and str(item.get("ClientReference", item.get("clientReference", ""))) == record.reference
            ), None)
        if not isinstance(data, dict):
            detail = str(payload.get("Message") or payload.get("message")
                         or payload.get("Description") or "")[:100]
            return "Hubtel message: " + detail if detail else "No matching provider transaction"
        state = str(data.get("TransactionStatus") or data.get("transactionStatus")
                    or data.get("Status") or data.get("status") or "").strip()
        if not state:
            return "No final status in provider response"
        prefix = "Callback claimed" if record.direction == "callback" else "Hubtel reported"
        return f"{prefix}: {state[:30]}"
    except (ValueError, UnicodeDecodeError, KeyError, TypeError):
        return "Non-JSON provider response"


@login_required
@require_GET
def diagnostics(request):
    permitted = _allowed(request)
    kind = request.GET.get("type", "")
    reference = (request.GET.get("reference") or "").strip()
    choices = dict(HubtelEvidence._meta.get_field("direction").choices)
    entries = HubtelEvidence.objects.filter(permitted)
    if reference:
        entries = entries.filter(reference=reference) if _reference_allowed(request, reference) else entries.none()
    if kind in choices:
        entries = entries.filter(direction=kind)
    entries = list(entries.order_by("-created_at", "-pk")[:150])
    for record in entries:
        record.hubtel_outcome = _provider_status(record)
    from core import pos_hubtel
    from core.models import HeldSale
    from core.views import branch_for
    branch = branch_for(request)
    pos_rows = HeldSale.objects.filter(
        branch=branch, label__startswith=pos_hubtel.LABEL_PREFIX
    ).select_related("user").order_by("-created_at")[:35]
    recent_pos = []
    for held in pos_rows:
        state = pos_hubtel.state_of(held)
        recent_pos.append({
            "reference": state.get("reference") or held.label[len(pos_hubtel.LABEL_PREFIX):],
            "status": state.get("status") or "unknown",
            "provider_status": state.get("provider_status") or "not_checked",
            "amount": state.get("amount") or "0.00",
            "check_count": int(state.get("check_count") or 0),
            "last_checked_at": state.get("last_checked_at"),
            "created_at": held.created_at,
        })
    return _private(render(request, "hubtel_direct/evidence.html", {
        "title": "Hubtel transaction evidence",
        "entries": entries, "kind": kind, "choices": choices,
        "reference": reference, "reference_valid": _reference_allowed(request, reference) if reference else False,
        "recent_pos": recent_pos,
    }))


@login_required
@require_POST
def check_status(request):
    """Management-only, independent provider GET. Never submits or replays money."""
    _allowed(request)
    ref = str(request.POST.get("reference") or "").strip()
    kind, row = _transaction_for_reference(request, ref) if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", ref) else ("", None)
    if not row:
        messages.error(request, "Original KOFAD transaction reference not found or not accessible.")
        return redirect("hubtel_diagnostics")
    try:
        if kind == "hosted":
            from marketplace.hubtel import reconcile
            result = reconcile(ref)
            status = str(result.get("status") if isinstance(result, dict) else result)
        elif kind == "staff_pos":
            from core import pos_hubtel
            data = pos_hubtel.reconcile(ref, force=True)
            status = data.get("status", "unknown")
        else:
            from . import client
            from .debt_momo import _gateway_transaction
            if kind == "debt_momo":
                tx = _gateway_transaction(row)
            else:
                tx = row
            status, _ = client.check(tx)
        messages.info(request, "Current saved transaction state: " + str(status)
                      + ". If the request is rate-limited, the next read-only Hubtel check remains scheduled. "
                        "See exact provider responses below.")
    except Exception as exc:
        # The GET may fail, be inconclusive or contain a mismatch. Capture of
        # an HTTP response happens before parsing; never claim a payment failed
        # merely because the status-check endpoint returned an error.
        messages.warning(request, "Provider status could not be safely confirmed: " + str(exc)[:200])
    from urllib.parse import urlencode
    return redirect(reverse("hubtel_diagnostics") + "?" + urlencode({"reference": ref}))


@login_required
@require_GET
def download_bundle(request):
    """One ZIP with byte-exact original provider callbacks and status results."""
    _allowed(request)
    reference = (request.GET.get("reference") or "").strip()
    if not _reference_allowed(request, reference):
        from django.http import Http404
        raise Http404("Unknown payment reference")
    records = list(HubtelEvidence.objects.filter(
        _allowed(request), reference=reference,
    ).order_by("-created_at", "-pk")[:80])
    if not records:
        from django.http import Http404
        raise Http404("No provider evidence captured for this reference")
    files = []
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for record in records:
            raw = decrypt_exchange(record)
            ext = "txt" if record.direction in {"wallet_query", "status_request", "direct_check"} else "json"
            name = f"{record.direction}-{record.pk}-hubtel-original.{ext}"
            archive.writestr(name, raw)
            files.append({
                "filename": name,
                "evidence_id": record.pk,
                "recorded_at_utc": record.created_at.isoformat(),
                "operation": record.get_direction_display(),
                "http_status": record.http_status,
                "byte_length": len(raw),
                "sha256": record.body_sha256,
                "body_is_original_provider_data": record.direction not in {
                    "status_request", "direct_check", "wallet_query",
                    "direct_request", "preapprove_req",
                },
            })
        archive.writestr("MANIFEST-KOFAD-METADATA.json", json.dumps({
            "report_type": "KOFAD evidence index; NOT a Hubtel response",
            "client_reference": reference,
            "note": "Each *hubtel-original file contains the captured original bytes. "
                    "Status-check GET files contain URL/query but no authorization headers. "
                    "Callbacks are untrusted; use the independent provider status response for payment state.",
            "files": files,
        }, indent=2))
    response = HttpResponse(output.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="hubtel-transaction-{reference[:36]}.zip"'
    return _private(response)


@login_required
@require_GET
def download(request, evidence_id):
    evidence = get_object_or_404(HubtelEvidence.objects.filter(_allowed(request)), pk=evidence_id)
    raw = decrypt_exchange(evidence)
    if not raw:
        # Hubtel sometimes answers 401/403 with no payload at all. An empty
        # attachment is unusable in Safari, so provide *clearly labelled*
        # locally generated HTTP metadata, never a fabricated Hubtel response.
        endpoint = {
            "wallet_verify": "https://rnv.hubtel.com/v2/merchantaccount/merchants/{merchant_account}/mobilemoney/verify",
            "bank_verify": "https://rnv.hubtel.com/v2/merchantaccount/merchants/{merchant_account}/bank/verify/{bank_code}/{account_number}",
            "direct_submit": "Hubtel Direct receive/send API (see reference)",
            "direct_status": "Hubtel Direct transaction-status API",
        }.get(evidence.direction)
        report = {
            "report_type": "KOFAD-generated Hubtel HTTP diagnostic, NOT a Hubtel JSON response",
            "evidence_id": evidence.pk,
            "reference": evidence.reference,
            "recorded_at_utc": evidence.created_at.isoformat(),
            "operation": evidence.get_direction_display(),
            "http_status": evidence.http_status,
            "method": "GET" if evidence.direction in {"wallet_verify", "bank_verify", "direct_status", "status_check"} else "POST",
            "endpoint_template": endpoint,
            "provider_response_body_bytes": 0,
            "provider_response_body": "",
            "provider_returned_json_payload": False,
            "explanation": "The actual Hubtel HTTP response body contained zero bytes. No provider JSON response exists for this attempt. HTTP status and timestamp are retained by KOFAD.",
        }
        raw = (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        content_type, extension = "application/json", "json"
        suffix = "-http-diagnostic"
    else:
        try:
            json.loads(raw)
            content_type, extension = "application/json", "json"
        except (ValueError, UnicodeDecodeError, TypeError):
            content_type, extension = "text/plain; charset=utf-8", "txt"
        suffix = ""
    response = HttpResponse(raw, content_type=content_type)
    response["Content-Disposition"] = (
        f'attachment; filename="hubtel-{evidence.direction}-{evidence.pk}{suffix}.{extension}"'
    )
    return _private(response)
