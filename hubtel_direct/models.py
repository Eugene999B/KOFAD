import uuid
from django.conf import settings
from django.db import models


class DirectTransaction(models.Model):
    """Durable immutable-intent record. Provider responses cannot originate a debit."""
    KINDS = [("receive", "Receive MoMo"), ("send_momo", "Send MoMo"), ("send_bank", "Send to bank")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client_reference = models.CharField(max_length=36, unique=True, editable=False)
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
    status = models.CharField(max_length=24, default="approval_pending", db_index=True)
    hubtel_transaction_id = models.CharField(max_length=100, blank=True)
    provider_code = models.CharField(max_length=20, blank=True)
    callback_seen_at = models.DateTimeField(null=True, blank=True)
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
