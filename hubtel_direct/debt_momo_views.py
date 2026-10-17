"""Staff collection status only. A refresh cannot initiate a second payment."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core import services
from core.views import branch_for
from . import debt_momo
from .models import DebtMomoCollection


def _accessible(request, collection_id):
    branch = branch_for(request)
    services.permit(request.user, branch, "operate_finance")
    return get_object_or_404(
        DebtMomoCollection.objects.select_related("party", "branch", "receipt"),
        pk=collection_id, branch=branch,
    )


@login_required
@require_GET
def status(request, collection_id):
    row = _accessible(request, collection_id)
    response = render(request, "hubtel_direct/debt_momo_status.html", {
        "title": "Mobile Money payment verification",
        "collection": row,
        "can_check": debt_momo.ready_for_check(row),
    })
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def check(request, collection_id):
    row = _accessible(request, collection_id)
    if row.status not in debt_momo.RECHECKABLE:
        messages.info(request, "This request already has a recorded outcome.")
    elif timezone.now() < row.created_at + debt_momo.CHECK_WAIT:
        messages.info(request, "Wait for the payment network to process the original request. Nothing has been charged again.")
    elif not debt_momo.ready_for_check(row):
        messages.info(request, "The latest check was just completed. Please wait before checking again.")
    else:
        try:
            row = debt_momo.reconcile(row.pk)
            messages.info(request, "The original payment reference was checked. Only confirmed funds can create a receipt.")
        except ValidationError as exc:
            messages.warning(request, str(exc))
    return redirect("hubtel_debt_momo_status", collection_id=row.pk)
