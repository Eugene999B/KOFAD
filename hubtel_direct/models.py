import uuid
from django.conf import settings
from django.db import models


class DirectTransaction(models.Model):
    """Durable immutable-intent record. Provider responses cannot originate a debit."""
    KINDS = [("receive", "Receive MoMo"), ("send_momo", "Send MoMo"), ("send_bank", "Send to bank")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client_reference = models.CharField(max_length=36, unique=True, editable=False)
    request_key = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    request_fingerprint = models.CharField(max_length=64, blank=True, editable=False)
    kind = models.CharField(max_length=16, choices=KINDS)
    order = models.ForeignKey("marketplace.OnlineOrder", null=True, blank=True, on_delete=models.PROTECT)
    customer = models.ForeignKey("marketplace.CustomerAccount", null=True, blank=True, on_delete=models.PROTECT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name="hubtel_direct_requests", on_delete=models.PROTECT)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name="hubtel_direct_approvals", on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    channel = models.CharField(max_length=20, blank=True)
    bank_code = models.CharField(max_length=6, blank=True)
    destination_encrypted = models.TextField()  # Fernet, no plain account/phone number
    destination_suffix = models.CharField(max_length=4, blank=True)
    recipient_name = models.CharField(max_length=140, blank=True)
    purpose = models.CharField(max_length=180, blank=True)
    status = models.CharField(max_length=24, default="approval_pending", db_index=True)
    hubtel_transaction_id = models.CharField(max_length=100, blank=True)
    provider_code = models.CharField(max_length=20, blank=True)
    callback_seen_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True, db_index=True)
    next_check_at = models.DateTimeField(null=True, blank=True, db_index=True)
    check_count = models.PositiveIntegerField(default=0)
    checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "next_check_at"], name="hubtel_direct_due_idx"),
            models.Index(fields=["order", "-created_at"], name="hubtel_direct_order_idx"),
        ]

    def __str__(self):
        return f"{self.kind} {self.client_reference[-8:]} {self.status}"



class DirectDebitSaleIntent(models.Model):
    """Draft checkout and one provider preapproval; no issued sale until approval.

    The JSON contains the cashier's entire proposed sale and must never be
    exposed via public status endpoints. Replaying request_key returns this
    original intent rather than issuing another sale or Hubtel request.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.UUIDField(unique=True)
    request_fingerprint = models.CharField(max_length=64)
    branch = models.ForeignKey("core.Branch", on_delete=models.PROTECT, related_name="direct_debit_sale_intents")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="direct_debit_sale_intents")
    party = models.ForeignKey("core.Party", null=True, blank=True, on_delete=models.PROTECT)
    checkout_payload = models.JSONField(default=dict)
    terms_snapshot = models.JSONField(default=dict)
    phone_encrypted = models.TextField()
    phone_fingerprint = models.CharField(max_length=64, db_index=True)
    phone_suffix = models.CharField(max_length=4)
    channel = models.CharField(max_length=28)
    frequency = models.CharField(max_length=12)
    first_due_date = models.DateField()
    final_due_date = models.DateField()
    quoted_total = models.DecimalField(max_digits=14, decimal_places=2)
    quoted_paid = models.DecimalField(max_digits=14, decimal_places=2)
    outstanding = models.DecimalField(max_digits=14, decimal_places=2)
    client_reference = models.CharField(max_length=36, unique=True)
    hubtel_preapproval_id = models.CharField(max_length=100, blank=True)
    verification_type = models.CharField(max_length=8, blank=True)
    otp_prefix = models.CharField(max_length=8, blank=True)
    status = models.CharField(max_length=20, default="draft", db_index=True)
    provider_approved_at = models.DateTimeField(null=True, blank=True)
    last_provider_check_at = models.DateTimeField(null=True, blank=True)
    next_provider_check_at = models.DateTimeField(null=True, blank=True, db_index=True)
    request_sent_at = models.DateTimeField(null=True, blank=True)
    callback_seen_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=240, blank=True)
    document = models.OneToOneField("core.Document", null=True, blank=True, on_delete=models.PROTECT)
    mandate = models.OneToOneField("hubtel_direct.DebtMandate", null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "next_provider_check_at"], name="hubtel_v2_intent_due_idx"),
            models.Index(fields=["branch", "-created_at"], name="hubtel_v2_intent_branch_idx"),
        ]


class DebtMandate(models.Model):
    """A bounded, customer-authorized repayment agreement, not blanket wallet access.

    Never consider staff-created terms as Hubtel preapproval. Provider approval
    must be independently checked and cancellation always wins over dispatch.
    """
    STATUSES = [
        ("draft", "Awaiting setup"), ("pending", "Awaiting customer authorization"),
        ("approved", "Approved by wallet holder"), ("cancel_pending", "Cancellation processing"),
        ("cancelled", "Cancelled"), ("suspended", "Suspended — not active"),
        ("expired_review", "Unsuccessful — approval timed out; clear provider request"),
        ("settled", "Settled"),
        ("failed", "Authorization failed"),
    ]
    FREQUENCIES = [
        ("once", "One charge when overdue"), ("daily", "Daily"), ("weekly", "Weekly"),
        ("monthly", "Monthly"), ("quarterly", "Quarterly"), ("yearly", "Yearly"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey("core.Branch", on_delete=models.PROTECT, related_name="hubtel_debt_mandates")
    party = models.ForeignKey("core.Party", on_delete=models.PROTECT, related_name="hubtel_debt_mandates")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="hubtel_debt_mandates_created")
    phone_encrypted = models.TextField()
    phone_fingerprint = models.CharField(max_length=64, db_index=True)
    phone_suffix = models.CharField(max_length=4)
    channel = models.CharField(max_length=28)
    client_reference = models.CharField(max_length=36, unique=True, editable=False)
    hubtel_preapproval_id = models.CharField(max_length=100, blank=True)
    verification_type = models.CharField(max_length=8, blank=True)
    otp_prefix = models.CharField(max_length=8, blank=True)
    status = models.CharField(max_length=18, choices=STATUSES, default="draft", db_index=True)
    frequency = models.CharField(max_length=12, choices=FREQUENCIES)
    first_due_date = models.DateField()
    next_due_date = models.DateField()
    amount_per_period = models.DecimalField(max_digits=14, decimal_places=2)
    maximum_total = models.DecimalField(max_digits=14, decimal_places=2)
    collected_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    lower_partial_payment = models.BooleanField(default=False)
    minimum_partial = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    maximum_attempts_per_period = models.PositiveSmallIntegerField(default=1)
    terms_snapshot = models.JSONField(default=dict)
    provider_approved_at = models.DateTimeField(null=True, blank=True)
    cancellation_requested_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    last_provider_check_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["branch", "status", "next_due_date"], name="hubtel_debt_due_idx"),
            models.Index(fields=["party", "-created_at"], name="hubtel_debt_party_idx"),
        ]
        constraints = [models.UniqueConstraint(fields=["phone_fingerprint"], condition=models.Q(status__in=["pending", "approved", "cancel_pending", "suspended", "expired_review"]), name="hubtel_one_active_mandate_wallet")]

    @property
    def rollover_unpaid(self):
        """Older approved contracts retain their original rollover behavior."""
        return self.terms_snapshot.get("rollover_unpaid", True) is not False



class DebtDebitAttempt(models.Model):
    """One immutable amount/reference; no reuse after unknown HTTP outcomes."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mandate = models.ForeignKey(DebtMandate, related_name="attempts", on_delete=models.PROTECT)
    reference = models.CharField(max_length=36, unique=True, editable=False)
    period_due_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=24, default="planned", db_index=True)
    hubtel_transaction_id = models.CharField(max_length=100, blank=True)
    provider_code = models.CharField(max_length=24, blank=True)
    # Set only after authenticated Hubtel status-check returned an exact
    # PAID reference, amount, currency and provider transaction identity.
    # A callback, missing reply or tentative status must never populate this.
    provider_verified_paid_at = models.DateTimeField(null=True, blank=True, db_index=True)
    collection_document = models.OneToOneField("core.Document", null=True, blank=True, on_delete=models.PROTECT)
    next_check_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["status", "next_check_at"], name="hubtel_debit_attempt_due")]



class WorkerTransferProfile(models.Model):
    """Owner-managed, provider-verified salary destination, encrypted at rest."""
    worker = models.OneToOneField("core.Worker", related_name="hubtel_salary_destination", on_delete=models.PROTECT)
    payment_type = models.CharField(max_length=10, choices=[("momo", "Mobile Money"), ("bank", "Bank")])
    channel = models.CharField(max_length=20, blank=True)
    bank_code = models.CharField(max_length=6, blank=True)
    destination_encrypted = models.TextField()
    destination_suffix = models.CharField(max_length=4)
    provider_name = models.CharField(max_length=140)
    verified_at = models.DateTimeField()
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)


class PayrollTransferBatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    period = models.OneToOneField("core.PayrollPeriod", related_name="hubtel_transfer_batch", on_delete=models.PROTECT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.PROTECT)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name="+", on_delete=models.PROTECT)
    status = models.CharField(max_length=24, default="draft", db_index=True)
    expected_amount = models.DecimalField(max_digits=14, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)


class PayrollTransferLine(models.Model):
    request_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    batch = models.ForeignKey(PayrollTransferBatch, related_name="lines", on_delete=models.PROTECT)
    entry = models.OneToOneField("core.PayrollEntry", related_name="hubtel_transfer_line", on_delete=models.PROTECT)
    destination = models.ForeignKey(WorkerTransferProfile, on_delete=models.PROTECT)
    encrypted_account_snapshot = models.TextField()
    recipient_name_snapshot = models.CharField(max_length=140)
    payment_type_snapshot = models.CharField(max_length=10, choices=[("momo", "Mobile Money"), ("bank", "Bank")], default="momo")
    channel_snapshot = models.CharField(max_length=20, blank=True)
    bank_code_snapshot = models.CharField(max_length=6, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=24, default="draft", db_index=True)
    transaction = models.OneToOneField(DirectTransaction, null=True, blank=True, on_delete=models.PROTECT)
    payroll_payment = models.OneToOneField("core.PayrollPayment", null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["batch", "status"], name="hubtel_pay_line_status_idx")]


class HubtelRefundCase(models.Model):
    """One provider refund request per approved return; never auto-repeat unknown POST."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    return_request = models.OneToOneField("marketplace.MarketReturnRequest", related_name="hubtel_refund_case", on_delete=models.PROTECT)
    original_order = models.OneToOneField("marketplace.OnlineOrder", related_name="hubtel_original_refund_case", null=True, blank=True, on_delete=models.PROTECT)
    original_hubtel_order_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    original_transaction_reference = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.PROTECT)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name="+", on_delete=models.PROTECT)
    status = models.CharField(max_length=24, default="review_pending", db_index=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_provider_code = models.CharField(max_length=24, blank=True)
    callback_seen_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class OnlineDebtSettings(models.Model):
    """Branch-scoped guardrails for NEW consenting debt mandates.

    Saving the policy never authorizes Hubtel preapproval or actual debits.
    Existing signed terms are immutable; automatic debit flags still live
    behind independent, default-off Railway environment switches.
    """
    branch = models.OneToOneField(
        "core.Branch", related_name="online_debt_settings",
        on_delete=models.PROTECT,
    )
    plan_creation_enabled = models.BooleanField(default=True)
    pause_auto_collection = models.BooleanField(default=True)
    allow_mtn = models.BooleanField(default=True)
    allow_telecel = models.BooleanField(default=True)
    allow_once = models.BooleanField(default=True)
    allow_daily = models.BooleanField(default=True)
    allow_weekly = models.BooleanField(default=True)
    allow_monthly = models.BooleanField(default=True)
    allow_quarterly = models.BooleanField(default=True)
    allow_yearly = models.BooleanField(default=True)
    default_frequency = models.CharField(
        max_length=12, choices=DebtMandate.FREQUENCIES, default="weekly",
    )
    min_open_balance = models.DecimalField(max_digits=14, decimal_places=2, default="1.00")
    max_open_balance = models.DecimalField(max_digits=14, decimal_places=2, default="10000.00")
    min_installment = models.DecimalField(max_digits=14, decimal_places=2, default="1.00")
    max_installment = models.DecimalField(max_digits=14, decimal_places=2, default="2000.00")
    min_first_due_days = models.PositiveSmallIntegerField(default=0)
    max_first_due_days = models.PositiveSmallIntegerField(default=90)
    auto_collection_grace_days = models.PositiveSmallIntegerField(default=0)
    collection_from_hour = models.PositiveSmallIntegerField(default=8)
    collection_until_hour = models.PositiveSmallIntegerField(default=19)
    skip_weekends = models.BooleanField(default=True)
    rollover_unpaid = models.BooleanField(default=True)
    daily_collection_limit = models.DecimalField(max_digits=14, decimal_places=2, default="5000.00")
    daily_attempt_limit = models.PositiveSmallIntegerField(default=20)
    pending_review_hours = models.PositiveSmallIntegerField(default=72)
    unresolved_poll_hours = models.PositiveSmallIntegerField(default=6)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", null=True, blank=True,
        on_delete=models.PROTECT,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Online debt settings"
        verbose_name_plural = "Online debt settings"

    def allows_frequency(self, frequency):
        return bool(getattr(self, "allow_" + str(frequency), False))

    def allows_channel(self, channel):
        channels = {
            "mtn-gh-direct-debit": self.allow_mtn,
            "vodafone-gh-direct-debit": self.allow_telecel,
        }
        return bool(channels.get(channel, False))

    def __str__(self):
        return f"Online debt policy: {self.branch}"


class DebtMomoCollection(models.Model):
    """One cashier-initiated, provider-verified MoMo collection for an existing debt.

    An accepted request is never a customer payment or ledger receipt.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_key = models.UUIDField(unique=True, editable=False)
    branch = models.ForeignKey("core.Branch", on_delete=models.PROTECT)
    party = models.ForeignKey("core.Party", on_delete=models.PROTECT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    channel = models.CharField(max_length=20)
    phone_encrypted = models.TextField(editable=False)
    phone_suffix = models.CharField(max_length=4)
    wallet_name = models.CharField(max_length=140)
    reference = models.CharField(max_length=36, unique=True)
    provider_transaction_id = models.CharField(max_length=100, blank=True)
    provider_code = models.CharField(max_length=24, blank=True)
    status = models.CharField(max_length=24, default="initializing", db_index=True)
    receipt = models.OneToOneField("core.Document", on_delete=models.PROTECT, null=True, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    next_check_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["status", "next_check_at"], name="hubtel_debt_momo_due_idx")]
