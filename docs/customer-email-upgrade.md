# KOFAD Customer Care, Recovery and Email Automations

## Deployment and provider boundaries

This change is additive. Do not remove the original website CNAMEs, Railway
verification DNS, existing Cloudflare Email Routing MX, or existing Gmail
destination rules. The secure Cloudflare inbound Worker remains a separate,
feature-gated service.

**Cloudflare Email Routing Free** accepts inbound business mail. KOFAD
generates and stores outbound messages. **Brevo free-tier HTTPS API** is
required to actually deliver external emails. Railway Hobby blocks direct
SMTP; do not try to use ports 25/465/587.

The following deployment environment variables are required on `kofad-web`
and `kofad-sms` for **external** email:

- `KOFAD_EMAIL_PROVIDER=brevo`
- `KOFAD_EMAIL_ENABLED=1`
- `KOFAD_BREVO_API_KEY`: the verified account secret, entered only into Railway
- `KOFAD_BREVO_SECURITY_FROM_EMAIL=security@kofadimpex.com`
- `KOFAD_BREVO_TRANSACTION_FROM_EMAIL=transactions@kofadimpex.com`
- `KOFAD_SUPPORT_REPLY_TO_EMAIL=support@kofadimpex.com`
- `KOFAD_EMAIL_CENTER_ENABLED=1`: only after verifying the Worker secret,
  database migrations and owner permission model
- `KOFAD_EMAIL_NOTIFICATIONS_ENABLED=1`: only after a controlled send test and
  confirmation of verified/consented recipient policy
- `KOFAD_BREVO_DAILY_LIMIT`: optional maximum per day; hard upper bound 300.
  Default 300, shared by one-time password emails, staff replies, reports and
  customer campaigns. Lower it if your account has a smaller allowance.

Cloudflare domain DKIM/SPF verification must use the records shown in the
business's Brevo account. Do not guess them or add two SPF TXT records.

## Customer care contact settings

The company manager opens **Settings > Customer-care numbers** to add multiple
Ghana business telephone or WhatsApp contacts, labels, ordering and visibility.
They appear on the public homepage and customer sign-in/recovery screens. The
existing Company phone and WhatsApp remain available until replaced.
Only add official business-controlled numbers.

## Password recovery

- Customer: SMS remains supported for accounts with a linked phone. A verified
  EmailIdentity on the same CustomerAccount enables a separate email OTP path,
  including customers without a linked phone. Codes are hashed and expire in
  10 minutes; failed attempts and changes to the account password invalidate
  access. Google-only customers can set a separate password after verifying
  their linked email.
- Staff: username-based recovery supports existing SMS or an address previously
  verified under the same staff identity. Codes expire in 10 minutes, password
  stamps prevent reuse and the system continues to enforce MFA/session rules.
- WhatsApp reset is **not yet enabled**. It must use a specifically approved
  authentication template with verified delivery and rate limits, not general
  customer-chat messages. The selector is intentionally disabled.
- Do not expose whether unknown emails/usernames belong to an active account.

## Email notices and business automations

The existing Market verified-payment receipt and finance alerts remain in the
`EmailNotice` outbox. Additional fulfillment status changes enqueue idempotent
customer order-update notices when the customer has a verified email and
notifications are enabled. Closing reports and staff invitations reuse existing
business email workflows. No new notice authorizes a payment: payment
confirmation always depends on verified provider settlement.

## Promotional email and free allowance

Customers now have an explicit separate **promotional email opt-in** in Account
Security. The default is OFF. Order-status preferences do not imply marketing
consent. Only system administrators can activate or pause campaigns at
`/email/campaigns/`; messages are queued individually for active, verified,
opted-in customers and the consent check repeats immediately before sending.
Unsent campaign messages can be suppressed by pausing the campaign.

The running Railway worker queues campaign batches and delivers through
Brevo's HTTPS API. A cross-process database row counts all KOFAD Brevo send
attempts and limits them to 300 per Africa/Accra day by default. **Provider
balance is not directly queried**: the interface explicitly labels its
remaining figure an estimated KOFAD-side allowance. API acceptance does not
prove inbox delivery. Failed mail retries conservatively; timeouts are
quarantined as uncertain rather than resent automatically.

## Verification checklist

1. Keep Cloudflare Email Routing forwarding to the verified business Gmail
   until signed worker ingestion is tested and monitoring is in place.
2. Check Django migrations, `python manage.py check`,
   `python manage.py makemigrations --check --dry-run`, all tests and browser
   smoke on mobile and desktop.
3. Test customer login and recovery UI without phone, then with SMS; check
   verified email, wrong code, expiry and password change invalidation.
4. Check staff recovery with a dedicated nonproduction staff identity.
5. Create a test promotional campaign with **one opted-in test recipient**;
   confirm it never reaches unverified or opted-out recipients.
6. Revoke the test recipient's consent while messages are queued and confirm
   suppression. Verify campaign pause and end-of-day quota rollover.
7. Confirm actual Brevo sending domain authentication and delivery using
   controlled external inboxes.
8. Verify Railway web and worker health after deployment and check that
   existing sales, inventory, management and marketplace UI are unchanged.

## Rollback

Disable the new Email Centre and external sender switches. Keep additive
migration tables intact and preserve customer data, payment records, audit
history, and the verified Cloudflare Gmail forwarding rules.
