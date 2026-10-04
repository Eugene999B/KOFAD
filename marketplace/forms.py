from decimal import Decimal

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from core.identity import normalize_ghana_phone
from .models import DeliveryZone, MarketListing


class MarketListingForm(forms.ModelForm):
    image = forms.FileField(
        required=False,
        label="Market product photo",
        help_text="JPEG, PNG, WebP, HEIC/HEIF and other supported phone images are compressed automatically.",
        widget=forms.ClearableFileInput(attrs={"accept": "image/*,.heic,.heif"}),
    )
    remove_image = forms.BooleanField(required=False, label="Remove current market photo")

    class Meta:
        model = MarketListing
        fields = ["enabled", "featured", "title", "description", "price_source", "sort_order"]
        labels = {
            "enabled": "Publish this product to KOFAD Market",
            "featured": "Feature this product",
            "title": "Market display name",
            "description": "Customer-facing description",
            "price_source": "Market selling price",
            "sort_order": "Display order",
        }
        help_texts = {
            "enabled": "Only published products appear to customers. Stock still comes from KOFAD inventory.",
            "featured": "Featured items receive stronger placement on the public Market.",
            "title": "Leave blank to use the normal product name.",
            "price_source": "Choose whether Market follows the product's retail or wholesale, unit or pack price.",
            "sort_order": "Lower numbers appear first.",
        }
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.product = product
        if product:
            disabled = []
            for value, label in self.fields["price_source"].choices:
                if getattr(product, value, None) is None:
                    disabled.append(value)
            self.fields["price_source"].help_text = (
                "The selected source follows the product price automatically. "
                + ("Unavailable on this product: " + ", ".join(disabled) + "." if disabled else "")
            )

    def clean(self):
        data = super().clean()
        if data.get("enabled") and self.product:
            source = data.get("price_source")
            if not source or getattr(self.product, source, None) is None:
                self.add_error("price_source", "Choose a selling price that is enabled on this product.")
            has_existing = bool(getattr(self.instance, "image_data", None))
            if not self.files.get("image") and not has_existing:
                self.add_error("image", "Add a product photo before publishing this item to Market.")
            if data.get("remove_image") and not self.files.get("image"):
                self.add_error("remove_image", "A published product must keep a photo. Upload a replacement or unpublish it first.")
        return data


class CustomerRegistrationForm(forms.Form):
    full_name = forms.CharField(max_length=140, label="Full name")
    email = forms.EmailField(required=False, label="Email address")
    password = forms.CharField(widget=forms.PasswordInput, label="Create password")
    password_confirm = forms.CharField(widget=forms.PasswordInput, label="Confirm password")

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password

    def clean(self):
        data = super().clean()
        if data.get("password") and data.get("password_confirm") and data["password"] != data["password_confirm"]:
            self.add_error("password_confirm", "The two passwords do not match.")
        return data


class CustomerLoginForm(forms.Form):
    phone = forms.CharField(max_length=30, label="Phone number")
    password = forms.CharField(widget=forms.PasswordInput, label="Password")

    def clean_phone(self):
        return normalize_ghana_phone(self.cleaned_data["phone"])


class CheckoutForm(forms.Form):
    fulfilment = forms.ChoiceField(choices=[("delivery", "Deliver to me"), ("pickup", "I will pick it up")])
    recipient_name = forms.CharField(max_length=140)
    phone = forms.CharField(max_length=30)
    email = forms.EmailField()
    delivery_zone = forms.ModelChoiceField(queryset=DeliveryZone.objects.none(), required=False, empty_label="Choose delivery area")
    region = forms.CharField(max_length=100, required=False)
    town = forms.CharField(max_length=120, required=False)
    address_line = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False, label="Delivery address")
    landmark = forms.CharField(max_length=220, required=False)
    ghana_post_gps = forms.CharField(max_length=40, required=False, label="GhanaPost GPS")
    latitude = forms.DecimalField(max_digits=9, decimal_places=6, required=False, widget=forms.HiddenInput)
    longitude = forms.DecimalField(max_digits=9, decimal_places=6, required=False, widget=forms.HiddenInput)
    customer_note = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False, label="Order note")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["delivery_zone"].queryset = DeliveryZone.objects.filter(active=True)

    def clean_phone(self):
        return normalize_ghana_phone(self.cleaned_data["phone"])

    def clean(self):
        data = super().clean()
        if data.get("fulfilment") == "delivery":
            for field in ("delivery_zone", "town", "address_line"):
                if not data.get(field):
                    self.add_error(field, "This is required for delivery.")
        else:
            data["delivery_zone"] = None
            for field in ("region", "town", "address_line", "landmark", "ghana_post_gps", "latitude", "longitude"):
                data[field] = None if field in ("latitude", "longitude") else ""
        return data


class PublicMessageForm(forms.Form):
    name = forms.CharField(max_length=140)
    phone = forms.CharField(max_length=30)
    subject = forms.CharField(max_length=180, initial="Product or order enquiry")
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), max_length=2000)

    def clean_phone(self):
        return normalize_ghana_phone(self.cleaned_data["phone"])


class StaffOrderUpdateForm(forms.Form):
    action = forms.ChoiceField(choices=[
        ("confirm", "Confirm order"), ("prepare", "Start preparing"),
        ("ready_pickup", "Ready for pickup"), ("dispatch", "Out for delivery"),
        ("complete_delivery", "Delivered"), ("complete_pickup", "Picked up"),
        ("cancel_unpaid", "Cancel unpaid order"),
    ])
    delivery_agent_name = forms.CharField(max_length=140, required=False)
    delivery_agent_phone = forms.CharField(max_length=30, required=False)
    handover_code = forms.CharField(max_length=12, required=False)
    note = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False, max_length=500)

    def clean_delivery_agent_phone(self):
        raw = self.cleaned_data.get("delivery_agent_phone", "").strip()
        return normalize_ghana_phone(raw) if raw else ""


class CustomerPasswordResetForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput, label="New password")
    password_confirm = forms.CharField(widget=forms.PasswordInput, label="Confirm new password")

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password

    def clean(self):
        data = super().clean()
        if data.get("password") and data.get("password_confirm") and data["password"] != data["password_confirm"]:
            self.add_error("password_confirm", "The two passwords do not match.")
        return data


class DeliveryZoneForm(forms.ModelForm):
    class Meta:
        model = DeliveryZone
        fields = ["name", "fee", "eta_text", "sort_order", "active"]
        labels = {
            "name": "Delivery area / zone",
            "fee": "Delivery fee (GHS)",
            "eta_text": "Expected delivery time",
            "sort_order": "Display order",
            "active": "Available to customers",
        }
        help_texts = {
            "eta_text": "Example: Same day, 1–2 business days, or Call to confirm.",
            "sort_order": "Lower numbers appear first at checkout.",
        }
