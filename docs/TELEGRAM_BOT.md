# KOFAD IMPEX — three specialized official Telegram bots

**Owner-approved architecture: three bots, NOT four.** A separate fourth
verification bot could not be created; therefore the original official
@KOFADIMPEXBOT is the ONLY Telegram bot for optional OTP/password recovery.
Customer and staff bots are independent accounts created in BotFather.
DeepSeek is a separate future integration and is not included.

## Bot responsibilities

1. **Official KOFAD IMPEX**: @KOFADIMPEXBOT.
   - Public company information, links to the specialized bots, support guidance.
   - Account-security information and **opt-in recovery code delivery** for
     customers AND staff linked to this exact official bot.
   - No private finance, staff alerts, customer purchase records or marketing.
2. **Customer bot**: BotFather username is configured in
   TELEGRAM_CUSTOMER_BOT_USERNAME (do NOT guess the actual issued username).
   - Own-order tracking, published products, customer service routing, opt-in
     transactional notifications and opted-in marketing.
   - No privileged KOFAD staff data, financial reports, OTP or password recovery.
3. **Staff / management bot**: username in TELEGRAM_STAFF_BOT_USERNAME.
   - Verified, role/branch-limited business summaries, daily verified closings,
     periodic management reports, cash variance warnings and stock alerts.
   - No customer session impersonation, OTP processing, password recovery or
     cross-branch data queries.

Each Telegram bot has its OWN bot API token, webhook URL and inbound webhook
secret. Webhook replay IDs are unique per bot. Each linked identity is scoped by
both the account type and the bot and is revoked when credentials change.

## Security requirements

- Telegram identity is never established by a matching phone number alone.
  A person signs into KOFAD, confirms the current KOFAD password, opens the
  appropriate bot's short-lived deep link, and returns to KOFAD to confirm
  the six-digit Telegram pairing code.
- For password reset, the user must independently link to the **official bot**
  while authenticated, explicitly opt in to recovery, and request a short-lived
  code on the KOFAD website. The code goes ONLY to that registered and linked
  official bot conversation. Existing staff/customer email/SMS recovery remains.
- A staff member with a customer account can link both account types to
  the official security bot independently. Staff and customer business data
  remain isolated by role, consent and branch checks.
- Every bot needs to have been started by the user; Telegram cannot send to a
  random phone number.
- Do not log or paste tokens, webhook secrets, passwords, pairing links, OTPs,
  private business reports, or customer data into GitHub commits or chat.
- Message queue does not automatically resend deliveries whose outcomes are
  uncertain, to avoid duplicates.
- All external AI model calls (including DeepSeek) remain absent in this phase.

## Routes

| Bot | Webhook on staff.kofadimpex.com | Website link |
| --- | --- | --- |
| Official + OTP | /telegram/webhook/ | /integrations/telegram/security/ and /market/integrations/telegram/security/ |
| Customer | /telegram/customer/webhook/ | /market/integrations/telegram/ |
| Staff | /telegram/staff/webhook/ | /integrations/telegram/ |

Recovery entry points stay:
- /integrations/telegram/recover/ for staff
- /market/integrations/telegram/recover/ for customers

## Protected Railway configuration

All values below apply only to **KOFAD**. Existing KOFAD Railway production
services track the branch railway-release; changes MUST be merged from the
current release baseline, never from old main snapshots.

### On kofad-web

Existing official bot:
- TELEGRAM_BOT_TOKEN — already set by owner, value must remain private
- TELEGRAM_WEBHOOK_SECRET — already set privately
- TELEGRAM_WEBHOOK_BASE_URL=https://staff.kofadimpex.com

New customer bot:
- TELEGRAM_CUSTOMER_BOT_TOKEN — set PRIVATELY from BotFather
- TELEGRAM_CUSTOMER_BOT_USERNAME — EXACT BotFather username, without @
- TELEGRAM_CUSTOMER_WEBHOOK_SECRET — newly generated and privately configured

New staff bot:
- TELEGRAM_STAFF_BOT_TOKEN — set PRIVATELY from BotFather
- TELEGRAM_STAFF_BOT_USERNAME — EXACT BotFather username, without @
- TELEGRAM_STAFF_WEBHOOK_SECRET — newly generated and privately configured

### On kofad-sms

This existing worker sends opt-in Telegram notifications using the same KOFAD
database. It needs the **three sending tokens** only:
- TELEGRAM_BOT_TOKEN — already set by owner
- TELEGRAM_CUSTOMER_BOT_TOKEN — same Customer Bot token as web
- TELEGRAM_STAFF_BOT_TOKEN — same Staff Bot token as web

DO NOT set webhook secrets on the worker; only web receives webhooks.

### Release flags: all OFF by default

On BOTH web and worker (as applicable):
- TELEGRAM_BOT_ENABLED=0
- TELEGRAM_CUSTOMER_BOT_ENABLED=0
- TELEGRAM_STAFF_BOT_ENABLED=0
- TELEGRAM_NOTIFICATIONS_ENABLED=0
- TELEGRAM_ACCOUNT_LINK_ENABLED=0
- TELEGRAM_BUSINESS_ANALYTICS_ENABLED=0
- TELEGRAM_RECOVERY_ENABLED=0

A missing flag means disabled. Set flags explicitly to 1 ONLY after CI tests,
migration review, runtime smoke tests, and correct token/secret validation.

### Webhook setup

In a properly secured KOFAD web runtime after code deployment:
- python manage.py setup_telegram_webhook --bot official
- python manage.py setup_telegram_webhook --bot customer
- python manage.py setup_telegram_webhook --bot staff

Inspect configuration without exposing tokens:
- python manage.py setup_telegram_webhook --bot customer --status

Remove just one bot:
- python manage.py setup_telegram_webhook --bot customer --remove

Do not register until the corresponding bot endpoint is ready to answer
requests. Commands never print secrets.

## Rollout and no-lost-feature rule

1. Confirm the current railway-release HEAD SHA and compare the full PR diff.
   Never use old branches to overwrite new KOFAD features.
2. Require all GitHub Actions to finish successfully: Django system checks,
   migrations, unit tests, staff/customer auth, native apps, notifications,
   Google weekly backups and payment integrations.
3. Apply ONLY additive Telegram migrations and verify live health.
4. Configure and verify three webhook routes and secrets independently.
5. Enable official /start and /ping first; verify that OTP only goes to the
   official bot and requires prior account linking and explicit recovery consent.
6. Enable customer bot pairing + own order tracking; verify foreign accounts
   cannot access each other's data or staff reports.
7. Enable staff bot pairing and branch-restricted reports; validate with a
   non-management staff user and owner/admin accounts.
8. Enable automated notifications only after opt-in and queue isolation tests.
9. Keep recovery disabled until separate OTP security testing is complete.
10. Do not use merge, reset, force-push, deletion, stale snapshot restore or
    rollback on production to activate a bot. Roll back behavior with the
    independent flags, not a destructive database migration.

**Until verification and a controlled production release, this PR remains a
DRAFT and production continues running existing KOFAD functionality.**
