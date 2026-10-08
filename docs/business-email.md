# KOFAD business email — free-tier HTTPS sending, addresses and inboxes

KOFAD can send verification and transaction notices with either:
- Brevo free tier: 300 sends/day, up to ~9,000 in a 30-day month, via HTTPS API.
- Gmail API: send-only OAuth from a linked company-owned Gmail/Workspace mailbox.

Both use HTTPS and the existing `kofad-sms` worker (no extra service).
The email system is **disabled until authenticated provider credentials are
configured in Railway**, and messages are only queued for verified recipients
with notification consent. No Google/Brevo account can be created by code.

## Recommended role addresses

- `security@kofadimpex.com` — verification and account security FROM address
- `transactions@kofadimpex.com` — order, payment and receipt FROM address
- `support@kofadimpex.com` — customer enquiries and reply-to address
- `accounts@kofadimpex.com` — human billing and finance correspondence
- `sales@kofadimpex.com` — quotations and sales replies

These are **planned addresses, not automatically created mailboxes**.

## Sending setup with Brevo (most free volume)

1. Create a Brevo company account at https://www.brevo.com.
2. Authenticate sending domain `kofadimpex.com` in Brevo, adding the exact
   DKIM/domain verification records that Brevo provides at the **authoritative
   DNS provider**. Respect the existing SPF/DMARC policies. Do not replace MX
   records or overwrite existing SPF; consolidate authorised senders.
3. Register/verify `security@...` and `transactions@...` as authenticated
   senders. Obtain a Brevo **API key**, not SMTP key, for the HTTPS API.
4. Add these secure Railway variables to BOTH `kofad-web` and `kofad-sms`:
   `KOFAD_EMAIL_PROVIDER=brevo`
   `KOFAD_EMAIL_ENABLED=1`
   `KOFAD_BREVO_API_KEY=<secret from Brevo>`
   `KOFAD_BREVO_SECURITY_FROM_EMAIL=security@kofadimpex.com`
   `KOFAD_BREVO_TRANSACTION_FROM_EMAIL=transactions@kofadimpex.com`
   `KOFAD_SUPPORT_REPLY_TO_EMAIL=support@kofadimpex.com`
5. Verify a mailbox from a staff or customer account and confirm actual inbox
   delivery and sender authentication before exposing automatic notifications.
6. Then enable `KOFAD_EMAIL_NOTIFICATIONS_ENABLED=1` on both services.
   End users must still individually opt in.
7. Monitor Brevo's daily quota and message acceptance/delivery; 201 means
   the API accepted the message, not proof it landed in the recipient's inbox.
8. When moving to Gmail API instead, use
   `KOFAD_EMAIL_PROVIDER=gmail_api` and follow `docs/email-activation.md`.

## Receiving support and other addresses

Transactional sending is separate from receiving mail. Choose **one** inbox
hosting/routing platform and verify the email destination before any MX edits:
- Google Workspace (paid per primary mailbox; multiple aliases included).
- Zoho Mail custom-domain free plan when available in your region.
- Cloudflare Email Routing (free inbound forwarding, requires the domain's DNS
  to be managed in the connected Cloudflare account).

Do not enable two competing inbound MX systems, replace an existing working
mailbox, or forward sensitive finance mail to unknown third-party recipients.
Create separate filtered labels/shared inbox access for billing and complaints.
Google Workspace aliases generally route to one mailbox; if several employees
must access support mail, configure a collaborative inbox or authorised
delegation. Confirm a live **incoming** test to each role address before
advertising it publicly.

## External account and DNS blocker

The currently connected Cloudflare account did not list the domain
`kofadimpex.com` when queried. Therefore automation cannot change its
authoritative DNS or provision its addresses from that connection.
Connect the actual DNS provider/domain account, or supply the precise domain
verification records to be installed through its admin interface.

The connected ChatGPT Gmail plugin can read/send the user's connected mailbox
within ChatGPT; its tokens are **not** reusable for KOFAD's production Google
OAuth clients, business Gmail API consent or SMTP sender. Google sign-in is
separate from these business mailboxes.
