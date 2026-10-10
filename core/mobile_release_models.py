"""Controlled public/native app notices. No device IDs or confidential messages."""
import re

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


CHANNELS = (("customer", "KOFAD Market"), ("staff", "KOFAD Staff"))
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class MobileReleasePolicy(models.Model):
    """Minimum supported Android version is changed by superusers only.

    A minimum is ignored by the app until an approved Android release URL and
    published version exist. This avoids locking users out before distribution.
    """
    channel = models.CharField(max_length=12, choices=CHANNELS, unique=True)
    minimum_android_version = models.CharField(max_length=24, blank=True)
    critical_update_reason = models.CharField(max_length=220, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Mobile release policies"

    def clean(self):
        if self.minimum_android_version and not SEMVER.fullmatch(self.minimum_android_version):
            raise ValidationError({"minimum_android_version": "Use a stable version such as 1.2.0."})
        if self.minimum_android_version and not self.critical_update_reason.strip():
            raise ValidationError({"critical_update_reason": "Explain why this update is mandatory."})

    def __str__(self):
        return f"{self.get_channel_display()} Android update policy"


class MobileNotice(models.Model):
    """Generic opt-in mobile announcements; NEVER store private account data."""
    PRIORITIES = (("info", "Information"), ("important", "Important"), ("urgent", "Urgent"))
    channel = models.CharField(max_length=12, choices=CHANNELS, db_index=True)
    title = models.CharField(max_length=90)
    message = models.CharField(max_length=280)
    priority = models.CharField(max_length=12, choices=PRIORITIES, default="info")
    enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def clean(self):
        if self.expires_at and self.created_at and self.expires_at <= self.created_at:
            raise ValidationError({"expires_at": "Expiry must be after publication."})

    def __str__(self):
        return f"{self.get_channel_display()}: {self.title}"
