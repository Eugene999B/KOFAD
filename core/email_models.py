"""Models for KOFAD's departmental email centre (no hosted mailboxes)."""
from django.conf import settings
from django.db import models
from django.db.models import Q


class EmailMailbox(models.Model):
    address = models.EmailField(unique=True)
    label = models.CharField(max_length=100)
    branch = models.ForeignKey("core.Branch", null=True, blank=True, on_delete=models.PROTECT)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["address"]

    def __str__(self):
        return self.address


class EmailMailboxMember(models.Model):
    mailbox = models.ForeignKey(EmailMailbox, related_name="members", on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="kofad_mailbox_memberships", on_delete=models.CASCADE)
    can_read = models.BooleanField(default=True)
    can_send = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["mailbox", "user"], name="unique_kofad_mailbox_member")]


class EmailLetter(models.Model):
    DIRECTION = [("inbound", "Incoming"), ("outbound", "Outgoing")]
    STATUS = [("received", "Received"), ("draft", "Awaiting review"), ("queued", "Queued"), ("sending", "Sending"),
              ("submitted", "Submitted to provider"), ("internal", "Delivered internally"),
              ("failed", "Failed"), ("uncertain", "Needs review"),
              ("suppressed", "Suppressed by recipient preference")]
    mailbox = models.ForeignKey(EmailMailbox, related_name="letters", on_delete=models.PROTECT)
    direction = models.CharField(max_length=8, choices=DIRECTION)
    status = models.CharField(max_length=12, choices=STATUS)
    from_address = models.EmailField()
    to_address = models.EmailField()
    subject = models.CharField(max_length=255, blank=True)
    body_text = models.TextField(blank=True)
    message_id = models.CharField(max_length=255, blank=True)
    in_reply_to = models.CharField(max_length=255, blank=True)
    fingerprint = models.CharField(max_length=64, blank=True)
    source_key = models.CharField(max_length=180, unique=True, blank=True, null=True)
    had_attachments = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   related_name="kofad_sent_letters", on_delete=models.SET_NULL)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    related_name="kofad_approved_letters", on_delete=models.SET_NULL)
    approved_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["mailbox", "fingerprint"],
                condition=Q(direction="inbound"),
                name="unique_kofad_inbound_fingerprint",
            )
        ]
        indexes = [
            models.Index(fields=["status", "next_attempt_at"], name="kofad_mail_delivery"),
            models.Index(fields=["mailbox", "direction", "created_at"], name="kofad_mail_list"),
        ]


class EmailDailyUsage(models.Model):
    """Local send-attempt allowance for Brevo; not a substitute for provider telemetry."""
    day = models.DateField(unique=True)
    attempted = models.PositiveIntegerField(default=0)
    accepted = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-day"]


class EmailCampaign(models.Model):
    """Owner-controlled promotional campaign; recipients must explicitly opt in."""
    title = models.CharField(max_length=150)
    subject = models.CharField(max_length=200)
    body = models.TextField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_queued_at = models.DateTimeField(null=True, blank=True)
    completed_queuing_at = models.DateTimeField(null=True, blank=True)
