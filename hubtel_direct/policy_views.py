"""Dedicated, company-managed, audited Online Debt settings."""
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render

from core import services as core_services
from core.views import protected

from .models import DebtMandate, OnlineDebtSettings
from .policy_forms import OnlineDebtSettingsForm


@protected("manage_company")
def online_debt_settings(request, branch):
    policy, _ = OnlineDebtSettings.objects.get_or_create(branch=branch)
    form = OnlineDebtSettingsForm(request.POST or None, instance=policy)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            # Use the same lock order as the debit dispatcher (branch, policy)
            # to prevent manager edits and money workers from deadlocking.
            core_services.lock_branch(branch)
            row = OnlineDebtSettings.objects.select_for_update().get(pk=policy.pk)
            previous = {field: str(getattr(row, field)) for field in form.fields}
            form.instance = row
            updated = form.save(commit=False)
            updated.changed_by = request.user
            updated.save()
            current = {field: str(getattr(updated, field)) for field in form.fields}
            core_services.audit(
                request.user, branch, "settings.online_debt.updated",
                str(updated.pk), {"before": previous, "after": current},
                category="finance", severity="high",
            )
        messages.success(request, "Online Debt policy saved for this location. Existing customer agreements are unchanged.")
        return redirect("online_debt_settings")

    status = {
        "provider_preapproval": getattr(settings, "HUBTEL_DEBT_PREAPPROVAL_ENABLED", False),
        "provider_charges": getattr(settings, "HUBTEL_DEBT_AUTO_DEBIT_ENABLED", False),
        "egress_confirmed": getattr(settings, "HUBTEL_DIRECT_EGRESS_CONFIRMED", False),
    }
    return render(request, "hubtel_direct/online_debt_settings.html", {
        "title": "Online Debt settings",
        "form": form, "policy": policy, "flags": status,
        "approved_count": DebtMandate.objects.filter(branch=branch, status="approved").count(),
        "pending_count": DebtMandate.objects.filter(branch=branch, status__in=["draft", "pending", "cancel_pending"]).count(),
    })
