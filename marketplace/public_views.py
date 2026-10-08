"""Public company pages and a private, throttled customer-feedback intake."""
from datetime import timedelta
from xml.sax.saxutils import escape

from django.conf import settings

from django import forms
from django.contrib import messages
from django.core.cache import cache
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.utils.crypto import salted_hmac

from core.identity import normalize_ghana_phone
from core.models import Branch
from .models import Conversation, ConversationMessage, DeliveryZone, MarketListing
from .public_content import PAGES, POLICY_VERSION
from .views import _market_context



@require_http_methods(["GET", "HEAD"])
def robots_txt(request):
    host = request.get_host().split(":")[0].lower().rstrip(".")
    if host == "staff.kofadimpex.com":
        body = "User-agent: *\nDisallow: /\n"
    elif host == "market.kofadimpex.com":
        body = (
            "User-agent: *\n"
            "Allow: /market/\n"
            "Disallow: /market/account/\n"
            "Disallow: /market/access/\n"
            "Disallow: /market/cart/\n"
            "Disallow: /market/checkout/\n"
            "Disallow: /market/orders/\n"
            "Disallow: /market/messages/\n"
            "Disallow: /market/location/\n"
            "Disallow: /market/payments/\n"
            "Disallow: /market/payment/\n"
        )
    else:
        body = (
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /technical-admin/\n"
            "Disallow: /workspace/\n"
            "Disallow: /administration/\n"
            "Disallow: /settings/\n"
            "Disallow: /api/\n"
        )
    body += f"Sitemap: {settings.PUBLIC_SITE_ORIGIN}/sitemap.xml\n"
    response = HttpResponse(body, content_type="text/plain; charset=utf-8")
    response["Cache-Control"] = "public, max-age=3600"
    return response


@require_http_methods(["GET", "HEAD"])
def sitemap_xml(request):
    public_paths = [
        ("/", "1.0", "weekly"),
        ("/about/", "0.8", "monthly"),
        ("/faq/", "0.7", "monthly"),
        ("/delivery/", "0.7", "monthly"),
        ("/returns-policy/", "0.6", "monthly"),
        ("/terms/", "0.4", "yearly"),
        ("/privacy/", "0.4", "yearly"),
        ("/contact/", "0.7", "monthly"),
    ]
    rows = [
        (settings.PUBLIC_SITE_ORIGIN + path, priority, frequency)
        for path, priority, frequency in public_paths
    ]
    rows.append((settings.MARKET_SITE_ORIGIN + "/market/", "0.9", "daily"))
    product_ids = MarketListing.objects.filter(
        enabled=True, product__active=True
    ).values_list("pk", flat=True).order_by("pk")
    rows.extend(
        (f"{settings.MARKET_SITE_ORIGIN}/market/products/{pk}/", "0.8", "daily")
        for pk in product_ids
    )
    items = "".join(
        "<url><loc>" + escape(url) + "</loc><changefreq>" + frequency
        + "</changefreq><priority>" + priority + "</priority></url>"
        for url, priority, frequency in rows
    )
    xml = '<?xml version="1.0" encoding="UTF-8"?>' + (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + items + "</urlset>"
    )
    response = HttpResponse(xml, content_type="application/xml; charset=utf-8")
    response["Cache-Control"] = "public, max-age=3600"
    return response


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
