"""Explicit opt-in FCM subscription tied to a revocable KOFAD native session.

The Firebase device token is encrypted at rest and is never exposed through a
public GET, staff report, browser cookie or user-facing event log.
"""
from django.db import models

class MobilePushSubscription(models.Model):
    device_session = models.OneToOneField(
        "marketplace.MobileDeviceSession", related_name="push_subscription",
        on_delete=models.CASCADE,
    )
    token_digest = models.CharField(max_length=64, unique=True)
    encrypted_token = models.TextField(editable=False)
    service_opt_in = models.BooleanField(default=False)
    marketing_opt_in = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "KOFAD push subscription " + str(self.pk)

class MobilePushOutbox(models.Model):
    """One intended remote notification to one consented native device.

    A claimed send is never blindly replayed. Unknown provider outcomes require
    reconciliation rather than duplicate lock-screen notifications.
    """

    subscription = models.ForeignKey(
        MobilePushSubscription, related_name="outbox", on_delete=models.CASCADE,
    )
    event_key = models.CharField(max_length=160, unique=True)
    kind = models.CharField(
        max_length=12, choices=[("service", "Service"), ("promotion", "Promotion")],
        default="service",
    )
    title = models.CharField(max_length=90)
    body = models.CharField(max_length=200)
    status = models.CharField(max_length=12, default="queued", choices=[
        ("queued", "Queued"), ("sending", "Sending"),
        ("sent", "Sent"), ("failed", "Failed"), ("uncertain", "Needs review"),
    ], db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    attempted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"], name="kof_push_outbox_due_idx"),
        ]
