# KOFAD business email — free-tier HTTPS sending, addresses and inboxes

KOFAD can send verification and transaction notices with either:
- Brevo free tier: 300 sends/day, up to ~9,000 in a 30-day month, via HTTPS API.
- Gmail API: send-only OAuth from a linked company-owned Gmail/Workspace mailbox.

Both use HTTPS and the existing `kofad-sms` worker (no extra service).
The email system is **disabled until authenticated provider credentials are
configured in Railway**, and messages are only queued for verified recipients
with notification consent. No Google/Brevo account can be created by code.

## Current KOFAD management identity

The management departmental mailbox is **management@kofadimpex.com**.
The old personal address `eugene@kofadimpex.com` is retired as a visible
department name and a new send-from identity. KOFAD maps authenticated
incoming messages and internal KOFAD mail for that historic address to the
same Management inbox when its Cloudflare routing continues to deliver them.

The database migration renames the existing management mailbox **in place**:
message history, assigned staff, saved signatures, private drafts, notes,
conversations and permissions keep their original mailbox ID. In case
`management@` and `eugene@` are already distinct populated mailboxes,
migration deliberately stops rather than merging confidential records or
permissions without review.

### External address verification

KOFAD's internal departmental mailbox address does not automatically mean
the external address is hosted, routable or authorised as a Brevo sender.

**To enable every department to send as itself:**

1. In Brevo, verify/authenticate the `kofadimpex.com` sender domain
   using the actual account-specific DKIM and DMARC records. Do not alter
   Cloudflare MX settings or paste arbitrary DNS records into the domain.
2. Under Brevo **Settings > Senders, Domains, IPs > Senders**, add one
   sender each for `business@`, `management@`, `info@`, `support@`,
   `sales@`, `accounts@`, `orders@`, `staff@`, `reports@` and the
   already-used `transactions@`. Keep the company name clearly visible;
   select the appropriate department name for each.
3. Check that Brevo marks every address **active** (a domain can be
   authenticated while an individual sender record is still missing).
4. Only after those sender records are verified, update the Railway
   `KOFAD_BREVO_REGISTERED_SENDERS` variable on the sending worker and web
   service to include the real, verified addresses. Unverified addresses
   must continue using the existing verified transactional sender with
   a departmental Reply-To.
5. For incoming mail, confirm each address is routed to the KOFAD Cloudflare
   Worker (or the approved fallback Gmail). Test external inbound and
   separate real outbound delivery using company-owned test inboxes.
6. If migration has retired `eugene@`, retain an inbound Cloudflare rule
   or catch-all for historic mail, **not** a separate newly created KOFAD
   departmental mailbox. It routes to `management@` for continuity.

An internal Django migration **cannot** register external senders in Brevo,
authenticate DNS or activate a Cloudflare Email Routing rule. Those steps
must be confirmed with the actually connected provider/domain accounts.

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
