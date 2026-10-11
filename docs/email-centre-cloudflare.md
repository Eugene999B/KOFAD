# KOFAD role-based Email Centre (safe rollout)

## Architecture

- Cloudflare **Free** Email Routing receives `@kofadimpex.com` mail.
- The optional Cloudflare Email Worker (`cloudflare/kofad-email-router.js`)
  posts a signed, bounded RFC822 message to
  `https://staff.kofadimpex.com/email/ingest/`.
- Django validates timestamp, HMAC, recipient and size, and saves plain text
  in PostgreSQL under the matching departmental mailbox.
- Staff sign into their existing KOFAD accounts. Authorized staff see only
  mailbox assignments with branch scoping; only staff also holding the
  `core.send_messages` permission may send from assigned addresses.
- Internal KOFAD-to-KOFAD messages are delivered directly into PostgreSQL.
- External human messages and automated reports are queued by KOFAD and
  delivered through the existing Brevo **HTTPS API** using `kofad-sms`.
- Cloudflare is **not** used for outbound delivery and needs no paid plan.
  Brevo's current free sending allowance is separate and subject to limits.

## Status after merging (feature flag defaults OFF)

No inbound routing is changed as part of this code change. Cloudflare's existing
forwarding rules and catch-all to the verified company Gmail remain intact.
No user, finance, customer or sale records are migrated or overwritten.
This feature does not store attachments or HTML email bodies. Messages with
attachments, HTML-only messages or larger than 1 MiB fall back to verified
Gmail, not KOFAD.

To activate safely:

1. Wait until Cloudflare Email Routing is active; confirm an external test to
   `support@kofadimpex.com` arrives at the verified company Gmail.
2. Create and verify a Brevo account and authenticate `kofadimpex.com` as
   a sender domain (add Brevo's actual DKIM/verification records to Cloudflare;
   **do not replace Cloudflare's MX or add multiple SPF records**).
3. Set the following on **both** `kofad-web` and `kofad-sms`:
   - `KOFAD_EMAIL_PROVIDER=brevo`
   - `KOFAD_EMAIL_ENABLED=1`
   - `KOFAD_BREVO_API_KEY=...` (secure secret, never commit)
   - `KOFAD_BREVO_SECURITY_FROM_EMAIL=security@kofadimpex.com`
   - `KOFAD_BREVO_TRANSACTION_FROM_EMAIL=transactions@kofadimpex.com`
   - `KOFAD_SUPPORT_REPLY_TO_EMAIL=support@kofadimpex.com`
4. Choose a strong independent 48+ character random
   `KOFAD_EMAIL_INGEST_SECRET`, assign it to `kofad-web` and add as
   Cloudflare Worker **secret**, not plain-text source.
5. Deploy the Email Worker from `cloudflare/kofad-email-router.js`; configure
   `FALLBACK_EMAIL=kofadimpexenterprise@gmail.com` (verified destination).
   **Do not point live routing rules to it yet.**
6. On `kofad-web`, set `KOFAD_EMAIL_CENTER_ENABLED=1`; use the staff
   `/email/` page to assign read/send rights. Test an internal message.
7. Test the Worker with a controlled external sender using ONE temporary rule.
   Ensure the signed KOFAD inbound message is visible to its authorized staff,
   verify failed ingestion forwards to Gmail, and verify duplicates are ignored.
8. Move the desired Cloudflare email patterns from Gmail forwarding to the Worker.
   To support future dynamic inbox addresses, route the catch-all to the Worker
   too. Unknown aliases, failures and oversized mail will be forwarded to the
   existing company Gmail by the Worker.
9. After verified sender delivery, set `KOFAD_EMAIL_CENTER_ENABLED=1`
   also on `kofad-sms` and verify the `deliver_outgoing` worker job.
   Existing email notices require their separate
   `KOFAD_EMAIL_NOTIFICATIONS_ENABLED=1` opt-in flag.
10. Only after reviewing verified staff email identities and notification
    consent, enable closing reports. Reports are queued exactly once per
    closing for superusers and branch-authorized report viewers.

## Safety and troubleshooting

- Railway Hobby blocks SMTP ports. This integration uses HTTPS APIs only.
- Never place Brevo credentials in GitHub, Cloudflare Worker source or chats.
- Cloudflare Worker processing on Free is subject to resource limits. Fallback
  is the verified business Gmail account, not an unverified destination.
- HMAC includes UTC Unix seconds, envelope recipient, and SHA-256 of raw MIME;
  messages outside a 5-minute window are rejected.
- Received mail only stores safe plain text. No HTML execution or attachment
  hosting is introduced. Missing or HTML-only parts require sender follow-up.
- Brevo HTTP 201 means **submitted**, not recipient delivered. Provider timeouts
  are marked **uncertain** and require review rather than blind re-sending.
- Existing KOFAD `core.email_identity` automatic notifications continue to
  use the existing outbox, consent and verification checks.
- Staff cannot grant themselves mailbox access. Owner actions are recorded in
  KOFAD's business audit trail.
- Roll back by setting `KOFAD_EMAIL_CENTER_ENABLED=0` on web and worker, and
  restore Cloudflare forwarding rules to Gmail. Keep the additive DB tables.
