"""Manage customer-care contact numbers shown on public and customer auth pages."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .admin_views import company_admin
from .identity import normalize_ghana_phone
from .models import CustomerServiceContact
from .services import audit


@company_admin
@require_http_methods(["GET", "POST"])
def settings(request, branch):
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "delete":
            row = get_object_or_404(CustomerServiceContact, pk=request.POST.get("id"))
            audit(request.user, branch, "customer_care.contact_removed", row.pk,
                  {"label": row.label, "channel": row.channel})
            row.delete()
            messages.success(request, "Customer-care contact removed.")
        elif action == "save":
            try:
                label = request.POST.get("label", "").strip()[:70]
                channel = request.POST.get("channel", "")
                number = normalize_ghana_phone(request.POST.get("number", "").strip())
                if not label or channel not in {"call", "whatsapp"}:
                    raise ValidationError("Enter a label and select phone or WhatsApp.")
                pk = request.POST.get("id", "").strip()
                row = get_object_or_404(CustomerServiceContact, pk=pk) if pk else CustomerServiceContact()
                row.label = label
                row.channel = channel
                row.number = number
                row.active = request.POST.get("active") == "on"
                row.sort_order = max(0, min(int(request.POST.get("sort_order", "0")), 99))
                row.save()
                audit(request.user, branch, "customer_care.contact_saved", row.pk,
                      {"label": row.label, "channel": row.channel, "active": row.active})
                messages.success(request, "Customer-care contact updated.")
            except (ValidationError, TypeError, ValueError) as exc:
                messages.error(request, "; ".join(exc.messages) if isinstance(exc, ValidationError) else "Invalid contact details.")
        else:
            messages.error(request, "Unknown action.")
        return redirect("customer_care_settings")
    return render(request, "customer_care_settings.html", {
        "title": "Customer Care Contacts",
        "contacts": CustomerServiceContact.objects.all(),
    })
