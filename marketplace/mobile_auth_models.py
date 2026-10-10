"""Native-client OAuth-style, PKCE-bound grants and revocable device sessions.
Only *hashes* of authorization codes and bearer credentials reach the database.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class MobileAuthorizationGrant(models.Model):
    code_hash = models.CharField(max_length=64, unique=True)
    channel = models.CharField(max_length=8, choices=[("customer","Customer"),("staff","Staff")])
    customer = models.ForeignKey("marketplace.CustomerAccount", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    staff_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    branch = models.ForeignKey("core.Branch", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    customer_credential_stamp = models.CharField(max_length=64, blank=True)
    staff_access_version = models.PositiveIntegerField(null=True, blank=True)
    staff_mfa_at = models.FloatField(null=True, blank=True)
    code_challenge = models.CharField(max_length=43)
    redirect_uri = models.CharField(max_length=128)
    client_id = models.CharField(max_length=40)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=["expires_at"])]


class MobileDeviceSession(models.Model):
    channel = models.CharField(max_length=8, choices=[("customer","Customer"),("staff","Staff")])
    customer = models.ForeignKey("marketplace.CustomerAccount", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    staff_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    branch = models.ForeignKey("core.Branch", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    customer_credential_stamp = models.CharField(max_length=64, blank=True)
    staff_access_version = models.PositiveIntegerField(null=True, blank=True)
    staff_mfa_at = models.FloatField(null=True, blank=True)
    access_hash = models.CharField(max_length=64, unique=True)
    refresh_hash = models.CharField(max_length=64, unique=True)
    access_expires_at = models.DateTimeField()
    refresh_expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_rotated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["refresh_expires_at"])]
