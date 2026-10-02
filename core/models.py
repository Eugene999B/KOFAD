import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
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
    session_version = models.PositiveIntegerField(default=1)
    totp_secret = models.CharField(max_length=64, blank=True)
    totp_last_step = models.BigIntegerField(default=-1)


class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, unique=True)
    failures = models.PositiveIntegerField(default=0)
    blocked_until = models.DateTimeField(null=True, blank=True)


class Company(models.Model):
    name = models.CharField(max_length=150, default="KOFAD IMPEX ENTERPRISE")
    phone = models.CharField(max_length=40, blank=True)
    address = models.TextField(blank=True)
    currency = models.CharField(max_length=3, default="GHS")
    receipt_footer = models.CharField(max_length=240, default="Thank you for trading with KOFAD.")
    closing_tolerance = models.DecimalField(max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    def __str__(self):
        return self.name


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
    KINDS = [("sale", "Sale"), ("purchase", "Purchase"), ("return", "Return"),
             ("expense", "Expense"), ("collection", "Debt payment"), ("supplier_payment", "Supplier payment")]
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
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2)
    total = models.DecimalField(max_digits=14, decimal_places=2)
    source_line = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)
    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0) & Q(factor__gt=0), name="positive_line_units"),
            models.CheckConstraint(condition=Q(total__gte=0) & Q(unit_price__gte=0) & Q(unit_cost__gte=0), name="positive_line_amounts"),
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
    party = models.ForeignKey(Party, on_delete=models.PROTECT)
    channel = models.CharField(max_length=12, choices=[("sms", "SMS"), ("whatsapp", "WhatsApp")])
    body = models.TextField()
    status = models.CharField(max_length=16, default="draft")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
