import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class CustomerAccount(models.Model):
    phone = models.CharField(max_length=20, unique=True)
    full_name = models.CharField(max_length=140)
    email = models.EmailField(blank=True)
    password_hash = models.CharField(max_length=160)
    verified_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["full_name", "phone"]

    def set_password(self, raw):
        self.password_hash = make_password(raw)

    def check_password(self, raw):
        return check_password(raw, self.password_hash)

    def __str__(self):
        return f"{self.full_name} · {self.phone}"


class MarketListing(models.Model):
    PRICE_SOURCES = [
        ("retail_unit", "Retail price · single unit"),
        ("retail_pack", "Retail price · full pack"),
        ("wholesale_unit", "Wholesale price · single unit"),
        ("wholesale_pack", "Wholesale price · full pack"),
    ]
    product = models.OneToOneField("core.Product", related_name="market_listing", on_delete=models.CASCADE)
    enabled = models.BooleanField(default=False)
    featured = models.BooleanField(default=False)
    title = models.CharField(max_length=160, blank=True)
    description = models.TextField(blank=True)
    price_source = models.CharField(max_length=24, choices=PRICE_SOURCES, default="retail_unit")
    sort_order = models.PositiveIntegerField(default=100)
    image_data = models.BinaryField(null=True, blank=True, editable=False)
    image_thumb = models.BinaryField(null=True, blank=True, editable=False)
    image_mime = models.CharField(max_length=40, blank=True, default="image/webp")
    image_name = models.CharField(max_length=180, blank=True)
    image_updated_at = models.DateTimeField(null=True, blank=True)
    image_url = models.URLField(blank=True, default="")
    image_credit = models.CharField(max_length=180, blank=True, default="")
    tags = models.CharField(max_length=320, blank=True, default="")
    highlights = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["sort_order", "product__name"]

    @property
    def display_name(self):
        return self.title.strip() or self.product.name

    @property
    def market_price(self):
        value = getattr(self.product, self.price_source, None)
        return value if value is not None else Decimal("0")

    @property
    def factor(self):
        return self.product.pack_size if self.price_source.endswith("_pack") else 1

    @property
    def selling_label(self):
        return self.product.pack_name if self.price_source.endswith("_pack") else self.product.base_unit

    def __str__(self):
        return self.display_name


class MarketListingImage(models.Model):
    listing = models.ForeignKey(MarketListing, related_name="gallery_images", on_delete=models.CASCADE)
    image_data = models.BinaryField(null=True, blank=True, editable=False)
    image_thumb = models.BinaryField(null=True, blank=True, editable=False)
    image_mime = models.CharField(max_length=40, blank=True, default="image/webp")
    image_name = models.CharField(max_length=180, blank=True)
    image_url = models.URLField(blank=True, default="")
    image_credit = models.CharField(max_length=180, blank=True, default="")
    alt_text = models.CharField(max_length=180, blank=True, default="")
    caption = models.CharField(max_length=220, blank=True, default="")
    sort_order = models.PositiveIntegerField(default=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "pk"]

    @property
    def has_image(self):
        return bool(self.image_data or self.image_url)


class DeliveryZone(models.Model):
    name = models.CharField(max_length=120)
    fee = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    eta_text = models.CharField(max_length=120, blank=True, default="")
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class CustomerAddress(models.Model):
    customer = models.ForeignKey(CustomerAccount, related_name="addresses", on_delete=models.CASCADE)
    label = models.CharField(max_length=60, default="Delivery address")
    recipient_name = models.CharField(max_length=140)
    phone = models.CharField(max_length=20)
    region = models.CharField(max_length=100, blank=True)
    town = models.CharField(max_length=120)
    address_line = models.TextField()
    landmark = models.CharField(max_length=220, blank=True)
    ghana_post_gps = models.CharField(max_length=40, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_default", "-created_at"]


class OnlineOrder(models.Model):
    STATUSES = [
        ("awaiting_payment", "Awaiting payment"),
        ("paid", "Paid"),
        ("confirmed", "Confirmed"),
        ("preparing", "Preparing"),
        ("ready_pickup", "Ready for pickup"),
        ("out_for_delivery", "Out for delivery"),
        ("delivered", "Delivered"),
        ("picked_up", "Picked up"),
        ("cancelled", "Cancelled"),
        ("refund_pending", "Refund pending"),
        ("refunded", "Refunded"),
    ]
    PAYMENT_STATUSES = [
        ("unpaid", "Unpaid"), ("initializing", "Initializing"), ("pending", "Pending"),
        ("paid", "Paid"), ("failed", "Failed"), ("refunded", "Refunded"),
    ]
    FULFILMENT = [("delivery", "Delivery"), ("pickup", "Pickup")]
    LEDGER = [("pending", "Pending"), ("posted", "Posted"), ("attention", "Needs attention")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    public_reference = models.CharField(max_length=32, unique=True)
    confirmed_reference = models.CharField(max_length=32, unique=True, null=True, blank=True)
    customer = models.ForeignKey(CustomerAccount, related_name="orders", on_delete=models.PROTECT)
    branch = models.ForeignKey("core.Branch", related_name="online_orders", on_delete=models.PROTECT)
    party = models.ForeignKey("core.Party", null=True, blank=True, related_name="online_orders", on_delete=models.PROTECT)
    sale_document = models.OneToOneField("core.Document", null=True, blank=True, related_name="online_order", on_delete=models.PROTECT)
    delivery_zone = models.ForeignKey(DeliveryZone, null=True, blank=True, on_delete=models.PROTECT)
    status = models.CharField(max_length=24, choices=STATUSES, default="awaiting_payment")
    payment_status = models.CharField(max_length=16, choices=PAYMENT_STATUSES, default="unpaid")
    ledger_status = models.CharField(max_length=16, choices=LEDGER, default="pending")
    fulfilment = models.CharField(max_length=12, choices=FULFILMENT, default="delivery")
    recipient_name = models.CharField(max_length=140)
    phone = models.CharField(max_length=20)
    email = models.EmailField()
    region = models.CharField(max_length=100, blank=True)
    town = models.CharField(max_length=120, blank=True)
    address_line = models.TextField(blank=True)
    landmark = models.CharField(max_length=220, blank=True)
    ghana_post_gps = models.CharField(max_length=40, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    customer_note = models.TextField(blank=True)
    staff_note = models.TextField(blank=True)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    delivery_fee = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    delivery_distance_km = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    delivery_distance_source = models.CharField(max_length=24, blank=True, default="")
    delivery_pricing_mode = models.CharField(max_length=16, blank=True, default="")
    delivery_origin_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    delivery_origin_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    delivery_route_polyline = models.TextField(blank=True, default="")
    delivery_duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    payment_reference = models.CharField(max_length=100, blank=True)
    payment_channel = models.CharField(max_length=40, blank=True)
    delivery_agent_name = models.CharField(max_length=140, blank=True)
    delivery_agent_phone = models.CharField(max_length=20, blank=True)
    estimated_delivery_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["branch", "status", "created_at"], name="market_order_status_idx"),
            models.Index(fields=["customer", "created_at"], name="market_customer_order_idx"),
        ]

    @property
    def customer_reference(self):
        return self.confirmed_reference or "Awaiting payment"

    def __str__(self):
        return self.public_reference


class OnlineOrderLine(models.Model):
    order = models.ForeignKey(OnlineOrder, related_name="lines", on_delete=models.PROTECT)
    product = models.ForeignKey("core.Product", on_delete=models.PROTECT)
    listing = models.ForeignKey(MarketListing, null=True, blank=True, on_delete=models.SET_NULL)
    sale_line = models.ForeignKey("core.Line", null=True, blank=True, related_name="online_order_lines", on_delete=models.PROTECT)
    description = models.CharField(max_length=180)
    sku = models.CharField(max_length=40)
    mode = models.CharField(max_length=40)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    factor = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["pk"]

    @property
    def base_units(self):
        return self.quantity * self.factor


class StockReservation(models.Model):
    order = models.ForeignKey(OnlineOrder, related_name="reservations", on_delete=models.CASCADE)
    branch = models.ForeignKey("core.Branch", on_delete=models.PROTECT)
    product = models.ForeignKey("core.Product", on_delete=models.PROTECT)
    units = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    expires_at = models.DateTimeField()
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["order", "product"], name="one_market_reservation_per_product")]
        indexes = [models.Index(fields=["branch", "product", "active", "expires_at"], name="market_stock_reservation_idx")]

    @property
    def expired(self):
        return self.expires_at <= timezone.now()


class MarketPaymentAttempt(models.Model):
    order = models.ForeignKey(OnlineOrder, related_name="payment_attempts", on_delete=models.PROTECT)
    provider = models.CharField(max_length=24, default="paystack")
    reference = models.CharField(max_length=100, unique=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="GHS")
    status = models.CharField(max_length=24, default="initialized")
    access_code = models.CharField(max_length=120, blank=True)
    authorization_url = models.URLField(blank=True)
    provider_message = models.CharField(max_length=240, blank=True)
    next_check_at = models.DateTimeField(null=True, blank=True, db_index=True)
    check_count = models.PositiveIntegerField(default=0)
    verification_summary = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class OrderEvent(models.Model):
    order = models.ForeignKey(OnlineOrder, related_name="events", on_delete=models.CASCADE)
    status = models.CharField(max_length=32)
    title = models.CharField(max_length=140)
    note = models.TextField(blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name="+", on_delete=models.PROTECT)
    customer_visible = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]


class DeliveryTrackingUpdate(models.Model):
    order = models.ForeignKey(OnlineOrder, related_name="delivery_updates", on_delete=models.CASCADE)
    status = models.CharField(max_length=40, blank=True, default="")
    note = models.CharField(max_length=320, blank=True, default="")
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name="+", on_delete=models.SET_NULL)
    customer_visible = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]


class WishlistItem(models.Model):
    customer = models.ForeignKey(CustomerAccount, related_name="wishlist_items", on_delete=models.CASCADE)
    listing = models.ForeignKey(MarketListing, related_name="wishlist_items", on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["customer", "listing"], name="unique_market_wishlist_item")]


class RecentView(models.Model):
    customer = models.ForeignKey(CustomerAccount, related_name="recent_views", on_delete=models.CASCADE)
    listing = models.ForeignKey(MarketListing, related_name="recent_views", on_delete=models.CASCADE)
    view_count = models.PositiveIntegerField(default=1)
    last_viewed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_viewed_at"]
        constraints = [models.UniqueConstraint(fields=["customer", "listing"], name="unique_market_recent_view")]


class MarketReturnRequest(models.Model):
    STATUSES = [
        ("requested", "Requested"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("processing", "Processing"),
        ("refund_attention", "Refund needs attention"),
        ("completed", "Completed"),
    ]
    RESOLUTIONS = [("refund", "Refund to original payment method")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(OnlineOrder, related_name="return_requests", on_delete=models.PROTECT)
    customer = models.ForeignKey(CustomerAccount, related_name="return_requests", on_delete=models.PROTECT)
    status = models.CharField(max_length=16, choices=STATUSES, default="requested")
    resolution = models.CharField(max_length=12, choices=RESOLUTIONS, default="refund")
    reason = models.TextField()
    customer_note = models.TextField(blank=True)
    staff_note = models.TextField(blank=True)
    core_return_request = models.ForeignKey("core.CustomerReturnRequest", null=True, blank=True, related_name="+", on_delete=models.PROTECT)
    refund_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    provider_refund_id = models.CharField(max_length=80, blank=True, default="")
    provider_refund_status = models.CharField(max_length=32, blank=True, default="")
    provider_refund_message = models.CharField(max_length=240, blank=True, default="")
    refund_initiated_at = models.DateTimeField(null=True, blank=True)
    refund_processed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name="+", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class MarketReturnRequestLine(models.Model):
    request = models.ForeignKey(MarketReturnRequest, related_name="lines", on_delete=models.CASCADE)
    order_line = models.ForeignKey(OnlineOrderLine, related_name="market_return_lines", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    condition = models.CharField(
        max_length=16,
        choices=[("sellable", "Unused / sellable"), ("damaged", "Damaged / faulty")],
        default="sellable",
    )

    class Meta:
        ordering = ["pk"]
        constraints = [models.UniqueConstraint(fields=["request", "order_line"], name="unique_market_return_order_line")]


class MarketReturnAttachment(models.Model):
    request = models.ForeignKey(MarketReturnRequest, related_name="attachments", on_delete=models.CASCADE)
    original_name = models.CharField(max_length=220)
    mime_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField(default=0)
    sha256 = models.CharField(max_length=64)
    data = models.BinaryField(editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]

    @property
    def is_image(self):
        return self.mime_type.startswith("image/")


class Conversation(models.Model):
    customer = models.ForeignKey(CustomerAccount, null=True, blank=True, related_name="conversations", on_delete=models.SET_NULL)
    order = models.ForeignKey(OnlineOrder, null=True, blank=True, related_name="conversations", on_delete=models.SET_NULL)
    public_name = models.CharField(max_length=140, blank=True)
    public_phone = models.CharField(max_length=20, blank=True)
    subject = models.CharField(max_length=180, default="Customer enquiry")
    status = models.CharField(max_length=12, choices=[("open", "Open"), ("closed", "Closed")], default="open")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name="market_conversations", on_delete=models.SET_NULL)
    accepted_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_reason = models.CharField(max_length=24, blank=True, default="")
    customer_typing_at = models.DateTimeField(null=True, blank=True)
    staff_typing_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]


class ConversationMessage(models.Model):
    SENDERS = [("visitor", "Visitor"), ("customer", "Customer"), ("staff", "Staff")]
    conversation = models.ForeignKey(Conversation, related_name="messages", on_delete=models.CASCADE)
    sender_type = models.CharField(max_length=12, choices=SENDERS)
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name="+", on_delete=models.SET_NULL)
    body = models.TextField()
    read_by_staff = models.BooleanField(default=False)
    read_by_customer = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]


class ConversationAttachment(models.Model):
    message = models.ForeignKey(ConversationMessage, related_name="attachments", on_delete=models.CASCADE)
    original_name = models.CharField(max_length=220)
    mime_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField(default=0)
    sha256 = models.CharField(max_length=64)
    data = models.BinaryField(editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]

    @property
    def is_image(self):
        return self.mime_type.startswith("image/")


class OtpThrottle(models.Model):
    PURPOSES = [("register", "Register"), ("reset", "Reset password"), ("login", "Customer login"), ("change_phone", "Change phone")]
    phone = models.CharField(max_length=20)
    purpose = models.CharField(max_length=12, choices=PURPOSES)
    send_count = models.PositiveIntegerField(default=0)
    attempts = models.PositiveIntegerField(default=0)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    blocked_until = models.DateTimeField(null=True, blank=True)
    code_digest = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["phone", "purpose"], name="one_market_otp_throttle")]


class PaymentConfiguration(models.Model):
    """One company-wide checkout provider; secrets stay in environment variables."""
    provider = models.CharField(max_length=24, choices=[("paystack", "Paystack"), ("hubtel", "Hubtel")], default="paystack")

    bank_account_name = models.CharField(max_length=140, blank=True)
    bank_account_number = models.CharField(max_length=40, blank=True)
    bank_name = models.CharField(max_length=100, blank=True)
    bank_branch = models.CharField(max_length=100, blank=True)
    bank_branch_code = models.CharField(max_length=20, blank=True)
    receiving_momo = models.CharField(max_length=20, blank=True)

    def save(self, *args, **kwargs):
        self.pk = 1
        return super().save(*args, **kwargs)
