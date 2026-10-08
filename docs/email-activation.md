# KOFAD email sign-in and Gmail/Workspace notification activation

The code is installed with email **disabled by default**. Staff and customers
continue using their existing username/phone and password until the sender is configured.
Phone recovery, existing MFA, passwords, and role/branch permissions stay mandatory.

## Administrator checklist

1. Choose an authorised business mailbox, preferably Google Workspace on
   `kofadimpex.com`, and verify domain ownership and SPF, DKIM, DMARC at your DNS provider.
2. Configure SMTP for that mailbox with an approved Google Workspace sending method.
   The initial implementation uses password-based SMTP; OAuth2/API sign-in is
   **not** enabled, and no Google social sign-in button is claimed.
3. Add these Railway environment variables **as secrets** to every process that
   needs email; never place them in GitHub code or chat:

   - `KOFAD_EMAIL_ENABLED=1`
   - `KOFAD_SMTP_HOST=smtp.gmail.com` (or your approved SMTP host)
   - `KOFAD_SMTP_PORT=587` (TLS)
   - `KOFAD_SMTP_USER=<authorised-mailbox>`
   - `KOFAD_SMTP_PASSWORD=<approved-app-password-or-SMTP-credential>`
   - `KOFAD_FROM_EMAIL=<verified-sender-address>`

4. Verify an email end-to-end: staff `My account` / customer `Account security`
   → send code → type six-digit code before expiry → sign in with the newly
   verified address **and the existing password**. Unverified profile emails
   never work as login aliases. Email codes expire after 10 minutes; at most
   3 sends/hour and 5 incorrect attempts per code.
5. Offer users a choice to opt in to transactional email notices.
   Set `KOFAD_EMAIL_NOTIFICATIONS_ENABLED=1` **only after** domain
   authentication, recipient consent, and sending quota checks.
6. Provision a separate scheduled Railway cron running
   `python manage.py send_email_notices --limit 25` periodically.
   Without this cron, notices stay in the outbox and will not be dispatched.
   Delivery is bounded and retried; outbound messages are never sent from
   a customer checkout HTTP request.
7. Test actual inbox delivery, spam folder, template readability, bounce handling,
   unsubscribe/consent, GDPR/Ghana data privacy obligations, and mail quotas
   before sending announcements or large volumes.

## Supported behaviour and limits

- Email/password login is **not** Google OAuth / 'Sign in with Google'.
- Email ownership is verified separately from untrusted profile contact fields.
- Staff still passes original password, branch permission, and MFA requirements.
- Customer phone remains linked and usable.
- Emails are opt-in, queue-backed, and disabled until external setup.
- Confirmed online-payment alerts can be queued for opted-in, report-authorised
  staff on the same branch; customer order and payment updates are also supported.
- Scheduled delivery is at-least-once; SMTP can occasionally deliver duplicates
  if the sender crashes after acceptance but before marking an item as sent.
- No secrets, MFA keys, or callback/payment JSON are emailed.
