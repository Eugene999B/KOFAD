"""Public company pages and a private, throttled customer-feedback intake."""
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.core.cache import cache
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.utils.crypto import salted_hmac

from core.identity import normalize_ghana_phone
from core.models import Branch
from .models import Conversation, ConversationMessage, DeliveryZone
from .public_content import PAGES, POLICY_VERSION
from .views import _market_context


class FeedbackForm(forms.Form):
    name = forms.CharField(max_length=140, label="Your name", widget=forms.TextInput(attrs={"autocomplete": "name"}))
    phone = forms.CharField(max_length=40, label="Phone number", widget=forms.TextInput(attrs={"type": "tel", "autocomplete": "tel"}))
    topic = forms.ChoiceField(choices=[
        ("Product enquiry", "Product enquiry"), ("Order support", "Order support"),
        ("Customer feedback", "Customer feedback"), ("Complaint", "Complaint"),
        ("Wholesale enquiry", "Wholesale enquiry"), ("Privacy request", "Privacy request"),
    ])
    message = forms.CharField(min_length=10, max_length=2000, widget=forms.Textarea(attrs={"rows": 6}))
    consent = forms.BooleanField(label="KOFAD may use these details to respond to my enquiry.")
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_phone(self):
        return normalize_ghana_phone(self.cleaned_data["phone"])

    def clean_website(self):
        if self.cleaned_data["website"]:
            raise forms.ValidationError("Please leave this field empty.")
        return ""


@require_http_methods(["GET", "HEAD", "POST"])
def public_page(request, slug):
    if slug not in PAGES:
        raise Http404
    page = PAGES[slug]
    topics = {"wholesale": "Wholesale enquiry", "product": "Product enquiry", "order": "Order support"}
    form = FeedbackForm(
        request.POST if request.method == "POST" else None,
        initial={"topic": topics.get(request.GET.get("topic"), "Product enquiry")},
    ) if slug == "contact" else None
    status = 200
    if request.method == "POST":
        if form is None:
            return render(request, "marketplace/public_page.html", _market_context(
                request, title=page["title"], page=page, page_slug=slug, policy_version=POLICY_VERSION,
            ), status=405)
        if form.is_valid():
            phone = form.cleaned_data["phone"]
            # No raw phone/IP in cache keys. Phone history survives worker restarts.
            ip_key = "public-feedback:" + salted_hmac("public-feedback", request.META.get("REMOTE_ADDR", "")).hexdigest()
            recent = Conversation.objects.filter(
                customer__isnull=True, public_phone=phone,
                created_at__gte=timezone.now() - timedelta(hours=1),
            ).count()
            if recent >= 3 or not cache.add(ip_key, True, 30):
                form.add_error(None, "Please wait before sending another message, or call our team for urgent help.")
                status = 429
            else:
                with transaction.atomic():
                    conversation = Conversation.objects.create(
                        branch=Branch.objects.filter(active=True).order_by("pk").first(),
                        public_name=form.cleaned_data["name"], public_phone=phone,
                        subject="Website · " + form.cleaned_data["topic"],
                    )
                    ConversationMessage.objects.create(
                        conversation=conversation, sender_type="visitor", body=form.cleaned_data["message"],
                        read_by_customer=True,
                    )
                messages.success(request, "Your message has been received. Our team can contact you on the number you provided.")
                return redirect("public_contact")
        else:
            status = 400
    template = {"about": "marketplace/about.html", "faq": "marketplace/help.html"}.get(
        slug, "marketplace/public_page.html"
    )
    return render(request, template, _market_context(
        request, title=page["title"], page=page, page_slug=slug, feedback_form=form,
        policy_version=POLICY_VERSION, public_pages=PAGES,
        delivery_zones=DeliveryZone.objects.filter(active=True) if slug == "delivery" else [],
    ), status=status)
