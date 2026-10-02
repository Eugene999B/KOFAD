import re

from django import forms

from .identity import normalize_ghana_phone
from .models import Company, Party, Product


class ProductForm(forms.ModelForm):
    pack_enabled = forms.BooleanField(
        required=False,
        initial=True,
        label="This product is stocked in packs / boxes",
        help_text="Turn this on when one carton, box, bundle or pack contains several sellable units.",
    )
    opening_packs = forms.IntegerField(
        required=False, min_value=0, initial=0,
        label="Opening full packs / boxes",
        help_text="Only shown when creating a product. KOFAD records this as opening stock evidence.",
    )
    opening_units = forms.IntegerField(
        required=False, min_value=0, initial=0,
        label="Opening loose units",
        help_text="Loose pieces already outside a full pack.",
    )

    class Meta:
        model = Product
        fields = ["name", "sku", "barcode", "category", "base_unit", "pack_name", "pack_size",
                  "cost", "retail_unit", "retail_pack", "wholesale_unit", "wholesale_pack", "reorder_level", "active"]
        labels = {
            "base_unit": "Smallest sellable unit",
            "pack_name": "Pack / box name",
            "pack_size": "Units inside one pack / box",
            "retail_unit": "Retail price · one loose unit",
            "retail_pack": "Retail price · one full pack",
            "wholesale_unit": "Wholesale price · one loose unit",
            "wholesale_pack": "Wholesale price · one full pack",
            "reorder_level": "Low-stock warning · base units",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["pack_enabled"].initial = bool(not self.instance.pk or self.instance.pack_size > 1)
        if self.instance.pk:
            self.fields.pop("opening_packs", None)
            self.fields.pop("opening_units", None)
        desired = ["name", "sku", "barcode", "category", "base_unit", "pack_enabled", "pack_name", "pack_size",
                   "opening_packs", "opening_units", "cost", "retail_unit", "retail_pack",
                   "wholesale_unit", "wholesale_pack", "reorder_level", "active"]
        self.order_fields([field for field in desired if field in self.fields])
        self.opening_total = 0

    def clean(self):
        data = super().clean()
        packed = bool(data.get("pack_enabled"))
        if packed:
            if (data.get("pack_size") or 0) < 2:
                self.add_error("pack_size", "A packed product must contain at least two base units per pack.")
            pack_size = data.get("pack_size") or 1
            loose = data.get("opening_units") or 0
            if not self.instance.pk and loose >= pack_size:
                self.add_error("opening_units", f"Loose opening units must be less than one full pack ({pack_size}).")
        else:
            data["pack_size"] = 1
            data["pack_name"] = data.get("base_unit") or "unit"
            data["retail_pack"] = None
            data["wholesale_pack"] = None
            data["opening_packs"] = 0

        if all(data.get(k) is None for k in ("retail_unit", "retail_pack", "wholesale_unit", "wholesale_pack")):
            raise forms.ValidationError("Enable at least one selling price. A blank price disables that selling mode.")

        if not self.instance.pk:
            self.opening_total = (data.get("opening_packs") or 0) * (data.get("pack_size") or 1) + (data.get("opening_units") or 0)
        return data


class PartyForm(forms.ModelForm):
    class Meta:
        model = Party
        fields = ["name", "phone", "email", "address", "credit_limit", "consent"]
        labels = {"phone": "Ghana phone number", "credit_limit": "Individual credit limit"}
        help_texts = {
            "phone": "Enter 0241234567, 241234567 or +233241234567. KOFAD stores +233241234567.",
            "credit_limit": "Zero means no individual customer cap; company credit policy still applies.",
        }

    def clean_phone(self):
        return normalize_ghana_phone(self.cleaned_data["phone"])


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = ["name", "phone", "address"]


class SalesPolicyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = [
            "allow_discounts", "staff_discount_limit", "max_discount_percent",
            "allow_price_overrides", "staff_price_reduction_limit", "max_price_reduction_percent",
            "allow_credit_sales", "max_credit_days", "max_credit_override",
            "customer_required_above", "sale_manager_threshold",
        ]
        labels = {
            "staff_discount_limit": "Staff discount limit (%)",
            "max_discount_percent": "Maximum discount (%)",
            "staff_price_reduction_limit": "Staff price reduction limit (%)",
            "max_price_reduction_percent": "Maximum price reduction (%)",
            "max_credit_days": "Maximum credit term (days)",
            "max_credit_override": "Maximum manager credit override",
            "customer_required_above": "Require a named customer above",
            "sale_manager_threshold": "Manager authority required above",
        }
        help_texts = {
            "allow_discounts": "When off, every sale uses the configured product price.",
            "staff_discount_limit": "Discounts above this percentage require a user with approval authority.",
            "max_discount_percent": "No user can discount beyond this percentage.",
            "allow_price_overrides": "Lets authorized staff replace the configured selling price at checkout.",
            "staff_price_reduction_limit": "Price reductions above this percentage require approval authority.",
            "max_price_reduction_percent": "Hard limit on how far a selling price may be reduced.",
            "allow_credit_sales": "When off, sales must be fully paid before posting.",
            "max_credit_days": "The due date cannot be farther away than this many days.",
            "max_credit_override": "How far a manager may exceed a customer's credit limit. Zero disables overrides.",
            "customer_required_above": "Zero disables the rule. Otherwise a named customer is required for sales at or above this amount.",
            "sale_manager_threshold": "Zero disables the rule. Otherwise sales above this total require approval authority.",
        }

    def clean(self):
        data = super().clean()
        staff_discount = data.get("staff_discount_limit") or 0
        max_discount = data.get("max_discount_percent") or 0
        staff_reduction = data.get("staff_price_reduction_limit") or 0
        max_reduction = data.get("max_price_reduction_percent") or 0
        if staff_discount > max_discount:
            self.add_error("staff_discount_limit", "Staff limit cannot exceed the maximum discount.")
        if staff_reduction > max_reduction:
            self.add_error("staff_price_reduction_limit", "Staff limit cannot exceed the maximum price reduction.")
        if data.get("allow_discounts") and max_discount <= 0:
            self.add_error("max_discount_percent", "Set a maximum discount greater than zero or turn discounts off.")
        if data.get("allow_price_overrides") and max_reduction <= 0:
            self.add_error("max_price_reduction_percent", "Set a maximum reduction greater than zero or turn price overrides off.")
        return data


class PaymentPolicyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = ["payment_cash", "payment_momo", "payment_bank", "payment_card"]
        labels = {
            "payment_cash": "Cash",
            "payment_momo": "Mobile Money (MoMo)",
            "payment_bank": "Bank transfer / deposit",
            "payment_card": "Card",
        }

    def clean(self):
        data = super().clean()
        if not any(data.get(field) for field in self.Meta.fields):
            raise forms.ValidationError("Keep at least one payment method enabled.")
        return data


class FinancePolicyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = ["expense_manager_threshold", "closing_tolerance"]
        labels = {
            "expense_manager_threshold": "Manager authority required for expenses above",
            "closing_tolerance": "Daily closing variance tolerance",
        }
        help_texts = {
            "expense_manager_threshold": "Zero disables the threshold. Above it, the person posting must have approval authority.",
            "closing_tolerance": "A variance beyond this amount requires a written explanation.",
        }


class ReceiptPolicyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = [
            "reference_prefix", "receipt_footer", "receipt_show_staff",
            "receipt_show_contact_phone", "receipt_show_payment_reference",
        ]
        labels = {
            "reference_prefix": "Reference prefix",
            "receipt_show_staff": "Show staff member on receipt",
            "receipt_show_contact_phone": "Show customer / supplier phone",
            "receipt_show_payment_reference": "Show bank / provider payment references",
        }
        help_texts = {
            "reference_prefix": "Optional 1–8 character prefix such as KOFAD. Existing transaction references never change.",
            "receipt_footer": "Printed on receipts and PDF copies.",
        }

    def clean_reference_prefix(self):
        value = self.cleaned_data["reference_prefix"].strip().upper()
        if value and not re.fullmatch(r"[A-Z0-9]+", value):
            raise forms.ValidationError("Use only letters and numbers in the reference prefix.")
        return value


class MessageTemplateForm(forms.ModelForm):
    class Meta:
        from .models import MessageTemplate
        model = MessageTemplate
        fields = ["name","body","active"]
    def clean_body(self):
        from .sms.templates import validate_template
        body = self.cleaned_data["body"]
        validate_template(body)
        return body
