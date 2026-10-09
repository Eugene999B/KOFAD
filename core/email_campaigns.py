"""Owner-only customer email campaigns with separate marketing consent."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .brevo_email import daily_limit, ready, usage_today
from .email_models import EmailCampaign, EmailLetter, EmailMailbox
from .services import audit

MAX_BATCH = 75
BACKLOG_LIMIT = 250


@require_http_methods(["GET", "POST"])
@login_required
def dashboard(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Only system administrators can manage bulk customer email.")
    if not settings.KOFAD_EMAIL_CENTER_ENABLED:
        from django.http import Http404
        raise Http404
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "create":
                subject = request.POST.get("subject", "").strip()
                title = request.POST.get("title", "").strip()
                body = request.POST.get("body", "").strip()
                if not subject or len(subject) > 200 or "\n" in subject or "\r" in subject:
                    raise ValidationError("Enter a valid subject up to 200 characters.")
                if not title or len(title) > 150 or not body or len(body) > 16000:
                    raise ValidationError("Enter a campaign name and a message up to 16,000 characters.")
                campaign = EmailCampaign.objects.create(
                    created_by=request.user, title=title, subject=subject, body=body,
                )
                audit(request.user, None, "email.campaign_created", campaign.pk,
                      {"title": title})
                messages.success(request, "Campaign saved as a draft; no emails have been sent.")
            elif action in {"activate", "pause"}:
                campaign = get_object_or_404(EmailCampaign, pk=request.POST.get("campaign_id"))
                if action == "activate":
                    if request.POST.get("confirm") != "yes":
                        raise ValidationError("Confirm that this campaign is for opted-in customers only.")
                    if not settings.KOFAD_EMAIL_ENABLED or not ready() or settings.KOFAD_EMAIL_PROVIDER != "brevo":
                        raise ValidationError("Connect and verify the business email provider before activating campaigns.")
                    campaign.active = True
                    messages.success(request, "Campaign approved. KOFAD will queue opted-in customers in batches.")
                else:
                    campaign.active = False
                    # Already sent emails cannot be recalled. Queued ones must be suppressed.
                    EmailLetter.objects.filter(
                        source_key__startswith=f"campaign:{campaign.pk}:",
                        status__in=["queued", "failed"],
                    ).update(status="suppressed", last_error="Campaign paused by owner.")
                    messages.success(request, "Campaign paused; unsent queued messages suppressed.")
                campaign.save(update_fields=["active"])
                audit(request.user, None, f"email.campaign_{action}", campaign.pk)
            else:
                raise ValidationError("Unknown campaign action.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("email_campaigns")
    from marketplace.models import EmailIdentity, CustomerAccount
    opted_in = EmailIdentity.objects.filter(
        kind="customer", verified_at__isnull=False, marketing_emails_enabled=True,
        owner_id__in=CustomerAccount.objects.filter(active=True).values("pk"),
    ).count()
    return render(request, "email_campaigns.html", {
        "title": "Email Campaigns", "campaigns": EmailCampaign.objects.order_by("-created_at")[:30],
        "usage": usage_today(), "provider_ready": bool(settings.KOFAD_EMAIL_ENABLED and ready()),
        "opted_in": opted_in, "pending": EmailLetter.objects.filter(
            direction="outbound", status__in=["queued", "failed"]).count(),
    })


def is_campaign_recipient_allowed(source_key, recipient):
    """Recheck marketing preference immediately before actual provider submission."""
    if not source_key or not source_key.startswith("campaign:"):
        return True
    parts = source_key.split(":")
    if len(parts) != 4 or parts[2] != "customer":
        return False
    try:
        int(parts[1])
        customer_id = int(parts[3])
    except ValueError:
        return False
    from marketplace.models import EmailIdentity, CustomerAccount
    return (
        EmailCampaign.objects.filter(pk=int(parts[1]), active=True).exists()
        and CustomerAccount.objects.filter(pk=customer_id, active=True).exists()
        and EmailIdentity.objects.filter(
            owner_id=customer_id, kind="customer", email=recipient,
            verified_at__isnull=False, marketing_emails_enabled=True,
        ).exists()
    )


def queue_active_campaigns(limit=MAX_BATCH):
    """Called by existing Railway worker, never by a customer's web request."""
    if not (settings.KOFAD_EMAIL_CENTER_ENABLED and settings.KOFAD_EMAIL_ENABLED
            and settings.KOFAD_EMAIL_PROVIDER == "brevo" and ready()):
        return 0
    if EmailLetter.objects.filter(direction="outbound",
                                  status__in=["queued", "failed"]).count() >= BACKLOG_LIMIT:
        return 0
    from marketplace.models import EmailIdentity, CustomerAccount
    mailbox = EmailMailbox.objects.filter(address="sales@kofadimpex.com", active=True).first()
    if not mailbox:
        return 0
    queued = 0
    for campaign in EmailCampaign.objects.filter(active=True).order_by("created_at")[:5]:
        eligible = EmailIdentity.objects.filter(
            kind="customer", verified_at__isnull=False,
            marketing_emails_enabled=True,
            owner_id__in=CustomerAccount.objects.filter(active=True).values("pk"),
        ).order_by("pk")
        remaining = False
        for identity in eligible.iterator(chunk_size=200):
            key = f"campaign:{campaign.pk}:customer:{identity.owner_id}"
            if EmailLetter.objects.filter(source_key=key).exists():
                continue
            if queued >= limit:
                remaining = True
                break
            footer = ("\n\nYou are receiving this because you opted in to KOFAD "
                      "promotional emails. Change your preference at "
                      "https://market.kofadimpex.com/market/account/security/")
            EmailLetter.objects.get_or_create(
                source_key=key,
                defaults=dict(mailbox=mailbox, direction="outbound", status="queued",
                              from_address=mailbox.address, to_address=identity.email,
                              subject=campaign.subject, body_text=campaign.body + footer,
                              next_attempt_at=timezone.now(), created_by=campaign.created_by),
            )
            queued += 1
        if not remaining:
            campaign.active = False
            campaign.last_queued_at = timezone.now()
            campaign.save(update_fields=["active", "last_queued_at"])
        elif queued >= limit:
            break
    return queued
