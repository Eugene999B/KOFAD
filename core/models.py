import uuid
from datetime import time
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q


class Branch(models.Model):
    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=12, unique=True)
    address = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    def __str__(self):
        return self.name


class Access(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    branches = models.ManyToManyField(Branch)
    recovery_phone = models.CharField(max_length=20, blank=True)
    session_version = models.PositiveIntegerField(default=1)
    must_change_password = models.BooleanField(default=False)
    force_password_change = models.BooleanField(default=False)
    totp_secret = models.CharField(max_length=64, blank=True)
    totp_last_step = models.BigIntegerField(default=-1)


class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, unique=True)
    failures = models.PositiveIntegerField(default=0)
    blocked_until = models.DateTimeField(null=True, blank=True)


class Company(models.Model):
    name = models.CharField(max_length=150, default="KOFAD IMPEX ENTERPRISE")
    phone = models.CharField(max_length=40, blank=True)
    secondary_phone = models.CharField(max_length=40, blank=True)
    address = models.TextField(blank=True)
    currency = models.CharField(max_length=3, default="GHS")

    # Payment channels can be switched off for new postings without rewriting historical ledgers.
    payment_cash = models.BooleanField(default=True)
    payment_momo = models.BooleanField(default=True)
    payment_bank = models.BooleanField(default=True)
    payment_card = models.BooleanField(default=True)

    # Sales and credit controls. Zero thresholds mean "no extra threshold".
    allow_discounts = models.BooleanField(default=False)
    staff_discount_limit = models.DecimalField(max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)])
    max_discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)])
    allow_price_overrides = models.BooleanField(default=False)
    staff_price_reduction_limit = models.DecimalField(max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)])
    max_price_reduction_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)])
    allow_credit_sales = models.BooleanField(default=True)
    max_credit_days = models.PositiveIntegerField(default=90, validators=[MinValueValidator(1)])
    max_credit_override = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        validators=[MinValueValidator(0)])
    customer_required_above = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        validators=[MinValueValidator(0)])
    sale_manager_threshold = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        validators=[MinValueValidator(0)])
    expense_manager_threshold = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        validators=[MinValueValidator(0)])

    # Receipt/reference presentation. The transaction UUID suffix remains the uniqueness boundary.
    reference_prefix = models.CharField(max_length=8, blank=True)
    receipt_footer = models.CharField(max_length=240, default="Thank you for trading with KOFAD.")
    receipt_show_staff = models.BooleanField(default=True)
    receipt_show_contact_phone = models.BooleanField(default=True)
    receipt_show_payment_reference = models.BooleanField(default=True)
    closing_tolerance = models.DecimalField(max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(0)])

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(staff_discount_limit__lte=models.F("max_discount_percent")),
                name="company_discount_limits_ordered"),
            models.CheckConstraint(condition=Q(staff_price_reduction_limit__lte=models.F("max_price_reduction_percent")),
                name="company_price_limits_ordered"),
        ]

    def __str__(self):
        return self.name


class DebtSettings(models.Model):
    DELIVERY = [("off", "Off"), ("draft", "Prepare drafts"), ("send", "Send SMS immediately")]
    GRACE_UNITS = [("days", "Days"), ("weeks", "Weeks"), ("months", "Months (30 days)")]

    delivery_mode = models.CharField(max_length=8, choices=DELIVERY, default="off")
    reminder_time = models.TimeField(default=time(9, 0))
    due_soon_enabled = models.BooleanField(default=True)
    due_soon_days = models.CharField(max_length=80, default="7,3,1")
    due_today_enabled = models.BooleanField(default=True)
    overdue_enabled = models.BooleanField(default=True)
    overdue_grace_value = models.PositiveIntegerField(default=0)
    overdue_grace_unit = models.CharField(max_length=8, choices=GRACE_UNITS, default="days")
    overdue_repeat_days = models.PositiveIntegerField(default=3, validators=[MinValueValidator(1)])
    max_sms_7_days = models.PositiveIntegerField(default=3, validators=[MinValueValidator(1)])
    max_sms_30_days = models.PositiveIntegerField(default=8, validators=[MinValueValidator(1)])
    minimum_hours_between_sms = models.PositiveIntegerField(default=24, validators=[MinValueValidator(1)])
    minimum_balance = models.DecimalField(max_digits=14, decimal_places=2, default=1, validators=[MinValueValidator(0)])
    skip_weekends = models.BooleanField(default=False)
    message_template = models.TextField(default=(
        "{company}: Dear {customer}, your outstanding balance is {currency} {balance} "
        "across {debt_count} receipt(s). {due_sentence} Please pay or contact us on {business_phone}. Thank you."
    ))

    def __str__(self):
        return "Debt reminder settings"

    @property
    def overdue_grace_days(self):
        multiplier = {"days": 1, "weeks": 7, "months": 30}[self.overdue_grace_unit]
        return self.overdue_grace_value * multiplier


class CommunicationSettings(models.Model):
    DELIVERY = DebtSettings.DELIVERY

    sale_receipt_mode = models.CharField(max_length=8, choices=DELIVERY, default="off")
    payment_confirmation_mode = models.CharField(max_length=8, choices=DELIVERY, default="off")
    daily_closing_mode = models.CharField(max_length=8, choices=DELIVERY, default="send")
    low_stock_mode = models.CharField(max_length=8, choices=DELIVERY, default="off")
    low_stock_time = models.TimeField(default=time(17, 0))
    closing_template = models.TextField(default=(
        "{company} closing {date} - Sales {currency} {sales_total}; cash expected {currency} {expected_cash}; "
        "cash counted {currency} {counted_cash}; variance {currency} {cash_variance}; "
        "debt collected {currency} {debt_collections}; expenses {currency} {expenses}. Submitted by {staff}."
    ))
    low_stock_template = models.TextField(default=(
        "{company} stock alert - {low_count} product(s) are low and {out_count} out of stock at {location}. "
        "Open Inventory for details."
    ))

    def __str__(self):
        return "Communication settings"


class ManagementContact(models.Model):
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20)
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.PROTECT)
    receive_closing = models.BooleanField(default=True)
    receive_low_stock = models.BooleanField(default=False)
    receive_system_alerts = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "pk"]

    def __str__(self):
        return f"{self.name} · {self.phone}"


class Product(models.Model):
    name = models.CharField(max_length=150)
    sku = models.CharField(max_length=40, unique=True)
    barcode = models.CharField(max_length=80, blank=True)
    category = models.CharField(max_length=80, blank=True)
    base_unit = models.CharField(max_length=24, default="piece")
    pack_name = models.CharField(max_length=24, default="carton")
    pack_size = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    cost = models.DecimalField(max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    retail_unit = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    retail_pack = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    wholesale_unit = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    wholesale_pack = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    reorder_level = models.PositiveIntegerField(default=10)
    active = models.BooleanField(default=True)
    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(condition=Q(pack_size__gte=1), name="positive_pack_size"),
            models.CheckConstraint(condition=Q(cost__gte=0), name="nonnegative_cost"),
        ]
    def __str__(self):
        return f"{self.sku} · {self.name}"


class Stock(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=0)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["branch", "product"], name="one_stock_balance")]


class Party(models.Model):
    KIND = [("customer", "Customer"), ("supplier", "Supplier")]
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    kind = models.CharField(max_length=10, choices=KIND)
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=40)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    credit_limit = models.DecimalField(max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    consent = models.BooleanField(default=False)
    class Meta:
        ordering = ["name"]
        constraints = [models.CheckConstraint(condition=Q(credit_limit__gte=0), name="nonnegative_credit_limit")]
    def __str__(self):
        return self.name


class Document(models.Model):
    KINDS = [("sale", "Sale"), ("purchase", "Purchase"), ("creditor_charge", "Creditor bill"), ("return", "Return"),
             ("supplier_return", "Supplier return"), ("inventory_writeoff", "Inventory write-off"), ("expense", "Expense"), ("collection", "Debt payment"),
             ("supplier_payment", "Supplier payment"), ("reversal", "Reversal")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=40, unique=True)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    kind = models.CharField(max_length=20, choices=KINDS)
    party = models.ForeignKey(Party, null=True, blank=True, on_delete=models.PROTECT)
    original = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)
    finalized = models.BooleanField(default=True, editable=False)
    total = models.DecimalField(max_digits=14, decimal_places=2)
    paid = models.DecimalField(max_digits=14, decimal_places=2)
    due_date = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)
    external_reference = models.CharField(max_length=120, blank=True, default="")
    payable_category = models.CharField(max_length=40, blank=True, default="")
    expense_category = models.CharField(max_length=40, blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["branch", "kind", "created_at"])]
        constraints = [
            models.CheckConstraint(condition=Q(total__gte=0), name="document_total_positive"),
            models.CheckConstraint(condition=Q(paid__gte=0) & Q(paid__lte=models.F("total")), name="document_paid_valid"),
        ]
        permissions = [
            ("operate_sales", "Complete sales"), ("operate_inventory", "Receive and request stock changes"),
            ("operate_finance", "Post expenses and payments"), ("approve_operations", "Approve stock operations and closings"),
            ("view_reports", "View business reports and cost"), ("manage_company", "Manage company configuration"),
        ]
    @property
    def balance(self):
        return self.total - self.paid
    def __str__(self):
        return self.reference


class Line(models.Model):
    document = models.ForeignKey(Document, related_name="lines", on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    description = models.CharField(max_length=150)
    mode = models.CharField(max_length=20)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    factor = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    list_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)])
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2)
    total = models.DecimalField(max_digits=14, decimal_places=2)
    source_line = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)
    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0) & Q(factor__gt=0), name="positive_line_units"),
            models.CheckConstraint(condition=Q(total__gte=0) & Q(unit_price__gte=0) & Q(unit_cost__gte=0), name="positive_line_amounts"),
            models.CheckConstraint(condition=Q(discount_percent__gte=0) & Q(discount_percent__lte=100),
                name="valid_line_discount"),
        ]


class Payment(models.Model):
    METHODS = [("cash", "Cash"), ("momo", "MoMo"), ("bank", "Bank"), ("card", "Card")]
    document = models.ForeignKey(Document, related_name="payments", on_delete=models.PROTECT)
    method = models.CharField(max_length=8, choices=METHODS)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reference = models.CharField(max_length=100, blank=True)
    direction = models.SmallIntegerField(default=1)
    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="positive_payment"),
            models.CheckConstraint(condition=Q(direction__in=[-1, 1]), name="payment_direction"),
        ]


class Allocation(models.Model):
    payment_document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="allocations")
    invoice = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="settlements")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="positive_allocation")]


class Movement(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    delta = models.IntegerField()
    balance = models.PositiveIntegerField()
    reference = models.CharField(max_length=50)
    reason = models.TextField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-created_at"]
        constraints = [models.CheckConstraint(condition=~Q(delta=0), name="nonzero_movement")]


class Operation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="operations")
    destination = models.ForeignKey(Branch, on_delete=models.PROTECT, null=True, blank=True, related_name="incoming_operations")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    kind = models.CharField(max_length=12, choices=[("adjustment", "Adjustment"), ("transfer", "Transfer")])
    quantity = models.IntegerField()
    reason = models.TextField()
    status = models.CharField(max_length=12, default="requested")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-created_at"]


class Closing(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    date = models.DateField()
    expected = models.JSONField()
    counted = models.JSONField()
    opening_cash = models.DecimalField(max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    cash_in = models.DecimalField(max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    cash_out = models.DecimalField(max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    summary = models.JSONField(default=dict)
    note = models.TextField(blank=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-date"]
        constraints = [models.UniqueConstraint(fields=["branch", "date"], name="one_closing_per_day")]


class HeldSale(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    label = models.CharField(max_length=100)
    cart = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)


class Idempotency(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    key = models.UUIDField()
    fingerprint = models.CharField(max_length=64)
    document = models.ForeignKey(Document, null=True, on_delete=models.PROTECT)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["branch", "key"], name="unique_request_key")]


class Audit(models.Model):
    branch = models.ForeignKey(Branch, null=True, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    action = models.CharField(max_length=80)
    reference = models.CharField(max_length=100)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-created_at"]


class Message(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    party = models.ForeignKey(Party, null=True, blank=True, on_delete=models.PROTECT)
    management_contact = models.ForeignKey(ManagementContact, null=True, blank=True, on_delete=models.PROTECT)
    channel = models.CharField(max_length=12, choices=[("sms", "SMS"), ("whatsapp", "WhatsApp")])
    body = models.TextField()
    status = models.CharField(max_length=16, default="draft")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    recipient = models.CharField(max_length=20, blank=True)
    recipient_name = models.CharField(max_length=120, blank=True)
    manual_override = models.BooleanField(default=False)
    provider = models.CharField(max_length=40, blank=True)
    sender = models.CharField(max_length=11, blank=True)
    sandbox = models.BooleanField(default=True)
    segments = models.PositiveIntegerField(default=1)
    encoding = models.CharField(max_length=12, default="gsm7")
    attempts = models.PositiveIntegerField(default=0)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name="+", on_delete=models.PROTECT)
    source_key = models.CharField(max_length=150, null=True, blank=True)
    last_error = models.CharField(max_length=240, blank=True)
    class Meta:
        ordering = ["-created_at"]
        permissions = [("send_messages", "Send and retry customer SMS")]
        constraints = [
            models.UniqueConstraint(fields=["branch", "source_key"], name="unique_message_source"),
            models.CheckConstraint(
                condition=(
                    Q(party__isnull=False, management_contact__isnull=True) |
                    Q(party__isnull=True, management_contact__isnull=False) |
                    (
                        Q(party__isnull=True, management_contact__isnull=True, manual_override=True)
                        & ~Q(recipient="")
                    )
                ),
                name="message_has_recipient",
            ),
        ]


class SmsAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(Message, related_name="delivery_attempts", on_delete=models.PROTECT)
    number = models.PositiveIntegerField()
    provider = models.CharField(max_length=40)
    provider_id = models.CharField(max_length=180, blank=True)
    status = models.CharField(max_length=20, default="sending")
    callback_digest = models.CharField(max_length=64)
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    http_status = models.PositiveIntegerField(null=True, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["message", "number"], name="unique_sms_attempt")]


class SmsEvent(models.Model):
    attempt = models.ForeignKey(SmsAttempt, on_delete=models.PROTECT)
    fingerprint = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=20)
    provider_id = models.CharField(max_length=180)
    created_at = models.DateTimeField(auto_now_add=True)


class MessageTemplate(models.Model):
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=100)
    body = models.TextField()
    active = models.BooleanField(default=True)
    def __str__(self):
        return self.name


class Correction(models.Model):
    refund_method = models.CharField(max_length=8, choices=Payment.METHODS, default="cash")
    original = models.OneToOneField(Document, related_name="correction", on_delete=models.PROTECT)
    posted = models.ForeignKey(Document, null=True, blank=True, related_name="+", on_delete=models.PROTECT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.PROTECT)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name="+", on_delete=models.PROTECT)
    reason = models.TextField()
    status = models.CharField(max_length=12, default="requested")
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-created_at"]





class SupplierReturn(models.Model):
    STATUS = [("requested", "Requested"), ("approved", "Approved"), ("rejected", "Rejected")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    source_line = models.ForeignKey(Line, related_name="supplier_returns", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    refund_method = models.CharField(max_length=8, choices=Payment.METHODS, default="cash")
    reason = models.TextField()
    status = models.CharField(max_length=12, choices=STATUS, default="requested")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.PROTECT)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", null=True, blank=True, on_delete=models.PROTECT)
    posted = models.OneToOneField(Document, related_name="+", null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="supplier_return_positive_quantity"),
        ]


class QuarantineItem(models.Model):
    STATUS = [
        ("requested", "Requested"), ("held", "Held in quarantine"), ("rejected", "Rejected"),
        ("released", "Released to sellable stock"), ("written_off", "Written off"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(0)])
    reason = models.TextField()
    status = models.CharField(max_length=16, choices=STATUS, default="requested")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.PROTECT)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", null=True, blank=True, on_delete=models.PROTECT)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", null=True, blank=True, on_delete=models.PROTECT)
    resolution_note = models.TextField(blank=True)
    loss_document = models.OneToOneField(Document, related_name="+", null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="quarantine_positive_quantity"),
            models.CheckConstraint(condition=Q(unit_cost__gte=0), name="quarantine_nonnegative_cost"),
        ]

    @property
    def value(self):
        return self.quantity * self.unit_cost


class StockCount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    scope = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=12, default="draft")
    note = models.TextField(blank=True)
    review_note = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.PROTECT)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        ordering = ["-created_at"]


class StockCountLine(models.Model):
    count = models.ForeignKey(StockCount, related_name="lines", on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    expected = models.PositiveIntegerField()
    movement_id = models.PositiveBigIntegerField(default=0)
    counted = models.PositiveIntegerField(null=True, blank=True)
    reason = models.CharField(max_length=240, blank=True)
    class Meta:
        ordering = ["product__name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["count", "product"], name="one_product_per_count"),
            models.CheckConstraint(condition=Q(counted__isnull=True) | Q(counted__lte=2000000000), name="count_quantity_limit"),
        ]
    @property
    def variance(self):
        return None if self.counted is None else self.counted - self.expected


class TransferReceipt(models.Model):
    operation = models.OneToOneField(Operation, related_name="receipt", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    note = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.PROTECT)
    recorded_at = models.DateTimeField(auto_now_add=True)
    resolution = models.CharField(max_length=12, blank=True)
    resolution_note = models.TextField(blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", null=True, blank=True, on_delete=models.PROTECT)
    resolved_at = models.DateTimeField(null=True, blank=True)
    loss_document = models.OneToOneField(Document, related_name="+", null=True, blank=True, on_delete=models.PROTECT)
    @property
    def missing(self):
        return self.operation.quantity - self.quantity


class PasswordRecovery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    phone = models.CharField(max_length=20)
    code_digest = models.CharField(max_length=64)
    password_stamp = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveIntegerField(default=0)
    used = models.BooleanField(default=False)
    sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class Worker(models.Model):
    EMPLOYMENT_TYPES = [
        ("permanent", "Permanent"), ("contract", "Contract"), ("casual", "Casual"),
        ("intern", "Intern / trainee"), ("probation", "Probation"),
    ]
    STATUSES = [
        ("active", "Active"), ("leave", "On leave"), ("suspended", "Suspended"), ("exited", "Exited"),
    ]
    SALARY_BASIS = [("monthly", "Monthly"), ("daily", "Daily"), ("hourly", "Hourly")]
    TAX_MODES = [
        ("resident", "Resident PAYE"), ("nonresident", "Non-resident"), ("casual", "Casual worker"), ("exempt", "Exempt"),
    ]

    employee_code = models.CharField(max_length=30, unique=True)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="workers")
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="worker_profile")
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    other_names = models.CharField(max_length=120, blank=True)
    preferred_name = models.CharField(max_length=80, blank=True)
    gender = models.CharField(max_length=20, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    nationality = models.CharField(max_length=60, default="Ghanaian")
    marital_status = models.CharField(max_length=30, blank=True)
    phone = models.CharField(max_length=30)
    alternate_phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    residential_address = models.TextField(blank=True)
    digital_address = models.CharField(max_length=80, blank=True)
    ghana_card_number = models.CharField(max_length=40, blank=True)
    tax_id = models.CharField(max_length=50, blank=True)
    ssnit_number = models.CharField(max_length=50, blank=True)
    department = models.CharField(max_length=100, blank=True)
    job_title = models.CharField(max_length=120)
    employment_type = models.CharField(max_length=20, choices=EMPLOYMENT_TYPES, default="permanent")
    status = models.CharField(max_length=20, choices=STATUSES, default="active")
    hire_date = models.DateField()
    contract_start = models.DateField(null=True, blank=True)
    contract_end = models.DateField(null=True, blank=True)
    exit_date = models.DateField(null=True, blank=True)
    exit_reason = models.TextField(blank=True)
    salary_basis = models.CharField(max_length=12, choices=SALARY_BASIS, default="monthly")
    base_salary = models.DecimalField(max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    recurring_allowance = models.DecimalField(max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    ssnit_enabled = models.BooleanField(default=True)
    tax_mode = models.CharField(max_length=14, choices=TAX_MODES, default="resident")
    junior_staff = models.BooleanField(default=False)
    bank_name = models.CharField(max_length=100, blank=True)
    bank_branch = models.CharField(max_length=100, blank=True)
    bank_account_name = models.CharField(max_length=120, blank=True)
    bank_account_number = models.CharField(max_length=80, blank=True)
    momo_network = models.CharField(max_length=40, blank=True)
    momo_number = models.CharField(max_length=30, blank=True)
    emergency_name = models.CharField(max_length=120, blank=True)
    emergency_relationship = models.CharField(max_length=60, blank=True)
    emergency_phone = models.CharField(max_length=30, blank=True)
    notes = models.TextField(blank=True)
    card_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name", "employee_code"]
        indexes = [
            models.Index(fields=["branch", "status", "department"], name="worker_branch_status_idx"),
            models.Index(fields=["employee_code"], name="worker_employee_code_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(base_salary__gte=0) & Q(recurring_allowance__gte=0), name="worker_compensation_nonnegative"),
        ]

    @property
    def full_name(self):
        return " ".join(part for part in [self.first_name, self.other_names, self.last_name] if part).strip()

    def __str__(self):
        return f"{self.employee_code} · {self.full_name}"


class WorkerDocument(models.Model):
    CATEGORIES = [
        ("photo", "Profile photo"), ("identity", "Identity document"), ("contract", "Employment contract"),
        ("certificate", "Certificate / qualification"), ("tax", "Tax document"), ("ssnit", "SSNIT document"),
        ("medical", "Medical / welfare"), ("disciplinary", "Disciplinary record"), ("other", "Other"),
    ]
    worker = models.ForeignKey(Worker, related_name="documents", on_delete=models.CASCADE)
    category = models.CharField(max_length=20, choices=CATEGORIES, default="other")
    title = models.CharField(max_length=180)
    document_type = models.CharField(max_length=100, blank=True)
    document_number = models.CharField(max_length=120, blank=True)
    original_filename = models.CharField(max_length=220)
    mime_type = models.CharField(max_length=100)
    file_size_bytes = models.PositiveIntegerField()
    checksum_sha256 = models.CharField(max_length=64)
    file_data = models.BinaryField()
    issued_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    is_current = models.BooleanField(default=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["worker", "category", "is_current"], name="worker_doc_current_idx")]


class PayrollRule(models.Model):
    code = models.CharField(max_length=40, default="GH-PAYROLL")
    name = models.CharField(max_length=160)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    resident_bands = models.JSONField(default=list)
    employee_ssnit_rate = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal("5.5"))
    employer_pension_rate = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal("13"))
    first_tier_rate = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal("13.5"))
    tier2_rate = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal("5"))
    min_insurable_earnings = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    max_insurable_earnings = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    nonresident_rate = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal("25"))
    casual_rate = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal("5"))
    bonus_rate = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal("5"))
    bonus_limit_percent = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal("15"))
    junior_overtime_rate = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal("5"))
    junior_overtime_excess_rate = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal("10"))
    junior_overtime_basic_limit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("18000"))
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_from", "-pk"]
        constraints = [models.UniqueConstraint(fields=["code", "effective_from"], name="payroll_rule_effective_version")]


class PayrollPeriod(models.Model):
    STATUSES = [
        ("draft", "Draft"), ("prepared", "Prepared for review"), ("approved", "Approved"),
        ("locked", "Locked for payment"), ("reconciled", "Reconciled"),
    ]
    branch = models.ForeignKey(Branch, related_name="payroll_periods", on_delete=models.PROTECT)
    year = models.PositiveIntegerField()
    month = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    start_date = models.DateField()
    end_date = models.DateField()
    rule = models.ForeignKey(PayrollRule, on_delete=models.PROTECT)
    status = models.CharField(max_length=16, choices=STATUSES, default="draft")
    note = models.TextField(blank=True)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", null=True, blank=True, on_delete=models.PROTECT)
    prepared_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", null=True, blank=True, on_delete=models.PROTECT)
    approved_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", null=True, blank=True, on_delete=models.PROTECT)
    locked_at = models.DateTimeField(null=True, blank=True)
    reconciled_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-year", "-month", "-pk"]
        constraints = [models.UniqueConstraint(fields=["branch", "year", "month"], name="one_payroll_period_per_branch_month")]
        indexes = [models.Index(fields=["branch", "year", "month", "status"], name="payroll_period_lookup_idx")]

    @property
    def label(self):
        return self.start_date.strftime("%B %Y")


class PayrollEntry(models.Model):
    period = models.ForeignKey(PayrollPeriod, related_name="entries", on_delete=models.PROTECT)
    worker = models.ForeignKey(Worker, related_name="payroll_entries", on_delete=models.PROTECT)
    basic_salary = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    allowances = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    bonus = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    overtime = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    other_earnings = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    pretax_relief = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    other_deductions = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    gross_pay = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    ssnit_employee = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    employer_pension = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    first_tier_remittance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tier2_contribution = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    chargeable_income = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    paye_tax = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    bonus_tax = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    overtime_tax = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    net_pay = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    calculation_snapshot = models.JSONField(default=dict)
    validation_flags = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["worker__last_name", "worker__first_name", "worker__employee_code"]
        constraints = [
            models.UniqueConstraint(fields=["period", "worker"], name="one_payroll_entry_per_worker_period"),
            models.CheckConstraint(condition=Q(paid_amount__gte=0), name="payroll_paid_nonnegative"),
        ]

    @property
    def balance(self):
        return max(Decimal("0"), self.net_pay - self.paid_amount)


class PayrollPayment(models.Model):
    entry = models.ForeignKey(PayrollEntry, related_name="payments", on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    method = models.CharField(max_length=12, choices=Payment.METHODS)
    reference = models.CharField(max_length=120, blank=True)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
