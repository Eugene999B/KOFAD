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
