import re

from django import forms

from .models import Company, Party, Product


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "sku", "barcode", "category", "base_unit", "pack_name", "pack_size",
                  "cost", "retail_unit", "retail_pack", "wholesale_unit", "wholesale_pack", "reorder_level", "active"]

    def clean(self):
        data = super().clean()
        if all(data.get(k) is None for k in ("retail_unit", "retail_pack", "wholesale_unit", "wholesale_pack")):
            raise forms.ValidationError("Enable at least one selling price. A blank price disables that mode.")
        return data


class PartyForm(forms.ModelForm):
    class Meta:
        model = Party
        fields = ["name", "phone", "email", "address", "credit_limit", "consent"]


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
        if data.get("staff_discount_limit", 0) > data.get("max_discount_percent", 0):
            self.add_error("staff_discount_limit", "Staff limit cannot exceed the maximum discount.")
        if data.get("staff_price_reduction_limit", 0) > data.get("max_price_reduction_percent", 0):
            self.add_error("staff_price_reduction_limit", "Staff limit cannot exceed the maximum price reduction.")
        if data.get("allow_discounts") and data.get("max_discount_percent", 0) <= 0:
            self.add_error("max_discount_percent", "Set a maximum discount greater than zero or turn discounts off.")
        if data.get("allow_price_overrides") and data.get("max_price_reduction_percent", 0) <= 0:
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
