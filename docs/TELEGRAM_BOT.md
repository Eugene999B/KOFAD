# KOFAD IMPEX — official enterprise Telegram assistant

Username: **@KOFADIMPEXBOT**.
Official bot: https://t.me/KOFADIMPEXBOT

## Critical release protection

Railway production uses **railway-release**, not main. This change was built as a
new additive feature branch FROM railway-release. Never force-push, restore a
stale branch, delete production history or replace KOFAD's existing services.
Before merging, compare current railway-release with the PR base. Merge only
after migrations, regression tests, and live-diff review have passed. Existing
financial, stock, payroll, WhatsApp, Android, email, and backup behaviors must
remain unchanged. DeepSeek is explicitly excluded from this release.

## Features implemented in this PR

- Authenticated Telegram webhook at /telegram/webhook/ (header-secret verified,
  private conversations only, replay protection, stale-update rejection).
- Website-to-Telegram double-confirmed account pairing for existing verified
  staff or customer accounts; website current password + short-lived Telegram
  code. Telegram handle/phone is never accepted as proof of identity.
- Live credential-stamp validation invalidates Telegram privileges when KOFAD
  password or staff session version changes.
- Customer own-order tracking by account association, published product search,
  contact and self-service sign-in routing.
- Staff READ-ONLY verified closing summaries for authorized branches (7d/30d).
  No arbitrary SQL queries, direct payments, refunds or permission changes.
- Independently opted-in customer order notices, staff closing notices, severe
  cash variance notices, stock shortages, weekly and monthly business digests.
- Durable idempotent outbox with kill switch, opt-in and permission checks at
  send time. Uncertain send outcomes are NEVER automatically retried to avoid
  duplicate alerts.
- Separately opt-in Telegram OTP password recovery for staff and customers
  using the phone already registered to their KOFAD account. Requires a prior
  strong website/Telegram account link, rate limitation and a fresh six-digit
  code delivered ONLY to that linked chat. Existing SMS and email reset flows
  are unchanged. Staff privileged MFA is not bypassed.
- Account pages with consent controls and explicit disconnect.
- Automated regression tests and manual deployment hooks.

## Not yet provided

- DeepSeek / external LLM intelligence (separate future update by request).
- Arbitrary staff instructions, autonomous refunds, privileged financial
  approvals, open-ended account database search, voice recognition.
- Bulk marketing campaigns, any unconsented unsolicited messages or sending
  Telegram messages to a phone number that has not started and linked the bot.
- Guaranteed Telegram OTP delivery: provider API may be unavailable; existing
  recovery alternatives remain available.

## Environment flags — off until testing

Add the token using Railway service Variables on **kofad-web**, not GitHub.
Keep private: never paste token or webhook secret into chats, logs or code.

TELEGRAM_BOT_TOKEN=<private secret already configured by owner>
TELEGRAM_WEBHOOK_SECRET=<separate random 32-byte URL-safe secret>
TELEGRAM_WEBHOOK_BASE_URL=https://staff.kofadimpex.com
TELEGRAM_BOT_ENABLED=0
TELEGRAM_ACCOUNT_LINK_ENABLED=0
TELEGRAM_BUSINESS_ANALYTICS_ENABLED=0
TELEGRAM_NOTIFICATIONS_ENABLED=0
TELEGRAM_RECOVERY_ENABLED=0

Roll out in order:
1. Apply migration 0047 via reviewed deployment after CI passing.
2. Configure secret and webhook host in Railway.
3. Enable TELEGRAM_BOT_ENABLED=1 and register webhook once in a securely
   configured runtime: python manage.py setup_telegram_webhook
4. Verify private chat /start, /ping, /help and rejection of spoofed requests.
5. Enable TELEGRAM_ACCOUNT_LINK_ENABLED=1; pair a TEST staff and customer
   account through KOFAD website, verify confirmation and permission boundaries.
6. Enable TELEGRAM_BUSINESS_ANALYTICS_ENABLED=1 after verifying branch-scoped
   reports in test accounts.
7. Enable TELEGRAM_NOTIFICATIONS_ENABLED=1 only after reviewing outbox
   consent and worker behavior. The existing kofad-sms worker processes
   messages independently, using the same shared PostgreSQL database.
8. Keep TELEGRAM_RECOVERY_ENABLED=0 until the owner verifies prior link,
   opt-in, wrong-code, expiry, throttling, staff session revocation and
   existing SMS/email fallbacks in a production-equivalent test environment.
   Only then enable it deliberately.

To inspect Telegram provider webhook without printing secrets:
python manage.py setup_telegram_webhook --status
To stop the provider webhook:
python manage.py setup_telegram_webhook --remove

## Important restrictions

Telegram bots **cannot** begin a conversation with a phone number. Users
must first open @KOFADIMPEXBOT, start the bot, and link their existing KOFAD
account. The KOFAD website-verified account phone is only an account lookup
hint for password recovery, not Telegram identity proof.

Private analytics use recorded VERIFIED closing records, not speculative
numbers. Staff roles and branch access are rechecked on every request,
and again before queued messages are sent. Marketing is separate from
transactional notices and requires explicit opt-in.

Production Telegram messages may be stored by Telegram; avoid full customer
addresses, passwords, OTP replies, financial credentials, or sensitive staff
details in normal chat.

## Support and rollback

Immediate disable: set TELEGRAM_BOT_ENABLED=0. Other flags are independent.
Never roll back financial migrations to disable the Telegram bot; leave the
additive tables in place and flip its flags. Preserve PostgreSQL data, KOFAD
history and deployed code. The Django management command may delete the bot
webhook if a hard disconnect is required.
