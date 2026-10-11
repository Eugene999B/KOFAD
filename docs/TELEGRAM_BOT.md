# Official KOFAD IMPEX Telegram bot

Bot handle: **@KOFADIMPEXBOT** (provided by the owner). Public link:
https://t.me/KOFADIMPEXBOT

## Scope of this foundation

This integration is intentionally disabled until the owner configures the BotFather
token privately. The initial webhook responds to private chat commands, directs
users to canonical KOFAD services, and **never** grants access to staff, customer
or financial records based on Telegram identifiers or claims in messages.

The following are **not yet built or enabled**: Telegram/KOFAD account binding,
staff management analytics, customer-specific order data returned in chat,
OTP delivery, marketing subscriptions, outbound notification queues, AI
conversation models, approvals, transactional business actions, and autonomous
financial operations. These require their own identity, permission, consent,
idempotency, audit and rollout designs. No one should advertise them as live.

## Owner setup

1. In Telegram, open the verified **@BotFather** account, use /mybots and select
   KOFADIMPEXBOT. Do not post or commit the BotFather token anywhere.
2. In Railway, open the **KOFAD web service** (not the SMS worker or database),
   select **Variables**, and store:
   - TELEGRAM_BOT_TOKEN (the real value from BotFather; secret)
   - TELEGRAM_WEBHOOK_SECRET (a separately generated random secret, not the bot token)
   - TELEGRAM_BOT_ENABLED=1
   - TELEGRAM_WEBHOOK_BASE_URL=https://staff.kofadimpex.com
3. Deploy the reviewed change only after CI passes.
4. Register the webhook *once*, from an environment that has those protected
   variables, by running:
   python manage.py setup_telegram_webhook
   Use the hosting service's **remote shell/exec** or a safe one-time job with
   the same protected variables. Do not copy the token into the command.
5. For diagnostic status:
   python manage.py setup_telegram_webhook --status
   To disconnect:
   python manage.py setup_telegram_webhook --remove
6. Open https://t.me/KOFADIMPEXBOT, press Start, send /ping and /help.
   A successful /ping reply confirms the inbound webhook and reply path.

The Telegram webhook accepts authenticated HTTPS POST requests at:
https://staff.kofadimpex.com/telegram/webhook/

We use the Telegram setWebhook secret_token option. Telegram sends that value
as the X-Telegram-Bot-Api-Secret-Token header. The Django handler checks it
with a constant-time comparison and rejects unauthenticated requests.

Telegram webhook responses can invoke sendMessage directly; this avoids a
second API call but does not provide a send receipt. Current messages are
limited to **private conversations** and use public-only commands.

## Secure future phases

1. Create a one-time, short-lived KOFAD **website-initiated** account-linking
   challenge. Require normal staff/customer login and current role/branch
   validation, separate account types and secure unlink/revoke controls.
2. Add a durable, idempotent, rate-limited notification outbox, consent flags,
   STOP/unsubscribe mechanism, logged delivery outcomes and retention.
3. Enable read-only KOFAD tools: public product search, per-customer order
   tracking, carefully scoped reports for authorized management.
4. Add approved second-factor delivery use cases where allowed, throttling,
   expiry, anti-replay and fallback. Never request password or OTP in chat.
5. AI chat can summarize *authorized* information through explicitly scoped,
   audited tools. Never allow the model to query arbitrary database tables,
   change payment state, issue refunds or grant privileged permissions.

## BotFather metadata suggestion

Name: KOFAD IMPEX

About: Official KOFAD business assistant for information, support and services.

Description: Welcome to KOFAD IMPEX. Get company information, browse products,
find customer accounts and support. More verified features are on the way.
Do not share passwords, PINs or OTPs.

Command list for /setcommands:

start - Open the KOFAD assistant
help - View available services
shop - Browse KOFAD products
orders - View your orders securely
track - Check order tracking options
account - Open your customer account
staff - Open the staff portal
analysis - Business insights information
alerts - Telegram notification status
otp - Verification and safety advice
support - Contact KOFAD
privacy - Privacy information
ping - Test the bot connection

## Security & operations

Do not send tokens through Telegram messages, ChatGPT, screenshots, public
GitHub files, or logs. Keep bot credentials in Railway protected environment
variables and rotate them via BotFather if exposed. Enforce KOFAD's role and
branch restrictions on all future private tool calls, not just on menus.
Always require user opt-in before outbound business/marketing notifications.
