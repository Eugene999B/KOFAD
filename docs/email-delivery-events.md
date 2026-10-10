# KOFAD Email Centre — verified Brevo delivery receipts

Status: **receiver implemented but disabled until the owner securely configures it**. This is different from provider-accepted / SMTP-submitted state.

## Why this matters

Brevo's API response `messageId` only proves that the provider accepted the request.
A verified Brevo transactional webhook can later confirm delivery or report
deferred, blocked, hard/soft bounce, invalid address, spam and unsubscribe.
Staff see the primary recipient's status in Email History; the owner sees
aggregate delivered/bounced totals under Email Reports. Per-recipient events
for explicitly entered CC and BCC recipients are stored separately without
claiming that the main customer was delivered.

## Secure activation (no credentials in GitHub)

1. Create a cryptographically random Bearer token of **at least 32 characters**.
   Store it as `KOFAD_BREVO_WEBHOOK_TOKEN` in the Railway **web service**
   environment only. Do not paste this token into issue comments, logs,
   screenshots, source control or staff messages.
2. In the authorised Brevo account create a **transactional** email webhook
   pointed to `https://<KOFAD_BACKEND_DOMAIN>/email/events/brevo/`.
   Use Brevo's documented webhook `auth` option with
   `{"type":"bearer","token":"<same secret>"}`.
   Do not send the token as a query-string URL parameter.
3. Enable `sent` / `request`, `delivered`, `deferred`, `hardBounce`,
   `softBounce`, `blocked`, `invalid`, `spam` and `unsubscribed` events.
   Opens and clicks are intentionally **not** recorded to protect privacy.
4. Add a source-IP allowlist according to Brevo's *current* documented
   webhook IP ranges as an additional control when supported by Cloudflare.
   Never infer trusted IPs from unchecked forwarded headers.
5. Use an **owner-controlled test email** first, verify provider acceptance
   and the event's status transition, then verify a simulated bounce.
   The endpoint rejects requests without the valid token and does not send mail.
6. Rotate the token in both services if suspected leaked. Remove the
   environment variable to disable the endpoint (HTTP 503).

## Reliability and privacy guarantees

- The callback accepts only POST application/json of at most 8 KiB, with
  a constant-time Bearer token comparison, recipient validation, supported
  event allowlist and 366-day timestamp window.
- Each provider ID / recipient / event / timestamp is stored once.
  Event ordering protects against late or replayed statuses overwriting
  more recent delivery information.
- The callback links to an existing EmailLetter by provider Message-ID or
  an opaque `kofad-letter-<id>` tag; it never creates a new message or sends
  a reply and never trusts a customer-controlled subject.
- The callback only updates the main recipient's status when the event
  email equals EmailLetter.to_address. CC/BCC events do not change that field.
- No email bodies, security codes, IP metadata or full Brevo webhook payloads
  are retained in EmailDeliveryEvent.
- The endpoint intentionally does **not** call Brevo to configure the webhook;
  activation requires the real Brevo account and a paired secret, neither
  of which belongs in a release migration.
- Historical emails sent before tags existed are matched conservatively by
  recorded provider Message-ID and recipient.

References:
- https://developers.brevo.com/docs/secured-webhooks
- https://developers.brevo.com/docs/transactional-webhooks
- https://developers.brevo.com/docs/send-a-transactional-email
