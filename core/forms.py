import re

from django import forms

from .identity import normalize_ghana_phone
from .models import Branch, CommunicationSettings, Company, DebtSettings, ManagementContact, Party, Product


class ProductForm(forms.ModelForm):
    pack_enabled = forms.ChoiceField(
        choices=[("yes", "Packed / boxed product"), ("no", "Loose / single-unit product")],
        initial="yes",
        label="Stock structure",
        help_text="Choose packed when one carton, box, bundle or pack contains several sellable units.",
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
            "retail_unit": "Retail price",
            "retail_pack": "Retail price · one full pack",
            "wholesale_unit": "Wholesale price",
            "wholesale_pack": "Wholesale price · one full pack",
            "reorder_level": "Low-stock warning · base units",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["pack_enabled"].initial = "yes" if (not self.instance.pk or self.instance.pack_size > 1) else "no"
        self.fields["pack_name"].required = False
        self.fields["pack_size"].required = False
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
        packed = data.get("pack_enabled") == "yes"
        if packed:
            if not str(data.get("pack_name") or "").strip():
                self.add_error("pack_name", "Enter the pack, box, carton or bundle name.")
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
        fields = ["name", "phone", "secondary_phone", "address"]
        labels = {
            "name": "Business name",
            "phone": "Business phone 1",
            "secondary_phone": "Business phone 2",
            "address": "Business address / public location",
        }
        help_texts = {
            "phone": "Printed as a KOFAD business contact number, not as the customer's number.",
            "secondary_phone": "Optional second public business number.",
            "address": "Public business address. Each store/location can also have its own address.",
        }

    def clean_phone(self):
        value = self.cleaned_data["phone"].strip()
        return normalize_ghana_phone(value) if value else ""

    def clean_secondary_phone(self):
        value = self.cleaned_data["secondary_phone"].strip()
        return normalize_ghana_phone(value) if value else ""


class LocationSettingsForm(forms.ModelForm):
    class Meta:
        model = Branch
        fields = ["name", "address"]
        labels = {"name": "Location name", "address": "Location address"}
        help_texts = {
            "name": "Shown on receipts, reports and stock records.",
            "address": "Printed on receipts for transactions posted at this location.",
        }


class DebtSettingsForm(forms.ModelForm):
    class Meta:
        model = DebtSettings
        fields = [
            "delivery_mode", "reminder_time", "due_soon_enabled", "due_soon_days",
            "due_today_enabled", "overdue_enabled", "overdue_grace_value", "overdue_grace_unit",
            "overdue_repeat_days", "max_sms_7_days", "max_sms_30_days",
            "minimum_hours_between_sms", "minimum_balance", "skip_weekends", "message_template",
        ]
        labels = {
            "delivery_mode": "Automatic reminder action",
            "reminder_time": "Reminder run time",
            "due_soon_days": "Due-soon reminder days",
            "overdue_grace_value": "Grace period before overdue",
            "overdue_grace_unit": "Grace period unit",
            "overdue_repeat_days": "Repeat overdue reminder every",
            "max_sms_7_days": "Maximum debt SMS in any 7 days",
            "max_sms_30_days": "Maximum debt SMS in any 30 days",
            "minimum_hours_between_sms": "Minimum hours between debt SMS",
            "minimum_balance": "Minimum balance for reminders",
            "message_template": "Default debt reminder message",
        }
        widgets = {"reminder_time": forms.TimeInput(attrs={"type": "time"})}
        help_texts = {
            "delivery_mode": "Off does nothing. Draft prepares messages for review. Queue submits automatically only when live SMS is configured.",
            "reminder_time": "Africa/Accra local time.",
            "due_soon_days": "Comma-separated days before due date, for example 7,3,1.",
            "overdue_grace_value": "Zero means a debt becomes overdue immediately after its due date.",
            "overdue_grace_unit": "Months are treated as 30 days for reminder scheduling.",
            "overdue_repeat_days": "How often an eligible overdue account may be reminded.",
            "minimum_balance": "Balances below this amount are ignored by automatic debt reminders.",
            "message_template": "Available placeholders: {company}, {customer}, {currency}, {balance}, {debt_count}, {due_sentence}, {business_phone}, {location}.",
        }

    def clean_due_soon_days(self):
        raw = self.cleaned_data["due_soon_days"]
        values = []
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            if not part.isdigit():
                raise forms.ValidationError("Use comma-separated whole days, such as 7,3,1.")
            value = int(part)
            if value < 0 or value > 3650:
                raise forms.ValidationError("Due-soon days must be between 0 and 3650.")
            values.append(value)
        if not values:
            raise forms.ValidationError("Enter at least one due-soon day.")
        return ",".join(str(value) for value in sorted(set(values), reverse=True))

    def clean_message_template(self):
        body = self.cleaned_data["message_template"].strip()
        allowed = {"company","customer","currency","balance","debt_count","due_sentence","business_phone","location"}
        keys = set(re.findall(r"\{([^{}]+)\}", body))
        if not keys.issubset(allowed):
            raise forms.ValidationError("The debt message contains an unsupported placeholder.")
        if len(body) > 1500:
            raise forms.ValidationError("Keep the debt message within 1,500 characters.")
        return body


class CommunicationSettingsForm(forms.ModelForm):
    class Meta:
        model = CommunicationSettings
        fields = [
            "sale_receipt_mode", "payment_confirmation_mode",
            "daily_closing_mode", "low_stock_mode", "low_stock_time",
            "closing_template", "low_stock_template",
        ]
        labels = {
            "sale_receipt_mode": "After a completed sale",
            "payment_confirmation_mode": "After a customer debt payment",
            "daily_closing_mode": "After daily closing",
            "low_stock_mode": "Daily low-stock summary",
            "low_stock_time": "Low-stock summary time",
            "closing_template": "Daily closing message",
            "low_stock_template": "Low-stock management message",
        }
        widgets = {
            "low_stock_time": forms.TimeInput(attrs={"type": "time"}),
            "closing_template": forms.Textarea(attrs={"rows": 5}),
            "low_stock_template": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {
            "sale_receipt_mode": "Customer must have messaging consent. Choose draft or send the receipt immediately.",
            "payment_confirmation_mode": "Customer must have messaging consent. Choose draft or send the confirmation immediately.",
            "daily_closing_mode": "When set to Send SMS immediately, closing summaries go straight to every active management number marked for closing.",
            "low_stock_mode": "When enabled, sends at most once per day to management contacts marked for stock notifications.",
            "low_stock_time": "Africa/Accra local time.",
            "closing_template": "Placeholders: {company}, {date}, {currency}, {sales_total}, {expected_cash}, {counted_cash}, {cash_variance}, {debt_collections}, {expenses}, {staff}, {location}.",
            "low_stock_template": "Placeholders: {company}, {low_count}, {out_count}, {location}.",
        }

    def clean(self):
        data = super().clean()
        allowed = {
            "closing_template": {"company","date","currency","sales_total","expected_cash","counted_cash","cash_variance","debt_collections","expenses","staff","location"},
            "low_stock_template": {"company","low_count","out_count","location"},
        }
        for field, tokens in allowed.items():
            body = (data.get(field) or "").strip()
            keys = set(re.findall(r"\{([^{}]+)\}", body))
            if not keys.issubset(tokens):
                self.add_error(field, "This template contains an unsupported placeholder.")
            if len(body) > 1500:
                self.add_error(field, "Keep this message within 1,500 characters.")
        return data


class ManagementContactForm(forms.ModelForm):
    class Meta:
        model = ManagementContact
        fields = ["name", "phone", "branch", "receive_closing", "receive_low_stock", "receive_system_alerts", "active"]
        labels = {
            "phone": "Ghana phone number",
            "branch": "Location scope",
            "receive_closing": "Receive daily closing messages",
            "receive_low_stock": "Receive low-stock summaries",
            "receive_system_alerts": "Receive system alerts",
        }
        help_texts = {"branch": "Leave blank to receive notifications for all locations."}

    def clean_phone(self):
        return normalize_ghana_phone(self.cleaned_data["phone"])


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
