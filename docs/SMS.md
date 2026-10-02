# Arkesel SMS and provider extensions

KOFAD now has an Arkesel adapter, durable database outbox, template drafts, recipient validation, segment estimates, constrained retries and delivery callbacks. Other providers integrate through the same adapter interface. No live provider request was made during development; automated tests use mocked responses.

## Deployment configuration

Set these on both the web service and a separate SMS worker:

- SMS_ENABLED=1
- SMS_PROVIDER=arkesel
- SMS_SENDER_ID=KOFAD (use a sender ID approved by Arkesel; maximum 11 characters)
- SMS_SANDBOX=1 initially
- SMS_PUBLIC_ORIGIN=https://YOUR-KOFAD-DOMAIN
- ARKESEL_API_KEY set privately in Railway
- The same DATABASE_URL and DJANGO_SECRET_KEY as the web service

Worker command: python manage.py process_sms --loop. A separate Railway worker can select Railway worker service settings as its configuration path; it must share the web service database and secrets.

For a bounded scheduled run: python manage.py process_sms --limit 100

Keep live sending disabled until the account, sender ID, consent records and sandbox contract have been verified. After a successful sandbox check, set SMS_SANDBOX=0 on both services for deliberate live submissions. No automatic switch to live mode exists.

## Daily workflow

Prepare a custom SMS in Communications or create a receipt/payment/reminder draft from a transaction. Review the recipient snapshot, message and estimated segments. A user with send_messages may queue it. The worker checks that the sender still has permission and the contact still consents before submission.

Receipt and payment drafts use recorded financial values. Debt reminders use the current outstanding balance. Owner-editable templates allow only explicit safe placeholders. Prepared debt reminders are revalidated before queueing and immediately before submission; a changed balance stops stale reminders. Queued message content is immutable; new values require a fresh draft.

Each attempt records provider, timestamp, status and provider ID. The main states are draft, queued, sending, accepted, delivered, undelivered, expired, failed, retry_wait, unknown and simulated. Provider acceptance is never displayed as handset delivery.

## Duplicate and failure control

A row lock claims each outbox item before network work. Duplicate queue requests cannot enqueue it twice. Each provider call creates a separate attempt. HTTP 429 may retry with exponential delay up to three attempts. Definite failures may be manually retried within that same limit.

Network failures, unrecognized responses and interrupted workers become unknown. They are not automatically resent or routed through a backup provider: the first provider may already have accepted and charged for them. Investigate the provider dashboard first. Callbacks can reconcile a subsequently confirmed result.

## Callbacks

Arkesel receives an attempt-specific callback URL with a random token. Only its SHA-256 digest is stored. Callback handling checks the token, attempt and provider message ID, deduplicates events and prevents delivered status from regressing.

Arkesel's published callback contract uses GET with sms_id and status. The callback uses possession of the per-attempt token; this is not described as an Arkesel-signed webhook. Gunicorn logs omit query strings so these tokens do not appear in application access logs. Configure any external proxy similarly.

Callbacks cannot mark sandbox submissions as live delivered messages. Verified callback events are retained as audit evidence.

## Adding another provider

Implement validate() and submit(recipient, body, sender, callback_url, sandbox), returning a Submission object. Register the class path in SMS_ADAPTERS, add provider-specific credentials as deployment variables, and implement/test its callback authentication and status mapping. Select it with SMS_PROVIDER for new messages.

Pending messages retain their selected provider, sender and sandbox setting. Adding a provider does not migrate in-flight submissions and never enables automatic failover.

## Verified reference contracts

- Arkesel send API and authentication: https://arkesel.com/developer-api/
- Sender length, sandbox and callback parameters: https://arkesel.com/sms-crm-integration-guide/
- CHALIN03 reference implementation: backend/services/smsService.js and smsReliabilityService.js in Eugene999B/chalin03-system-2

Provider contracts may change; revalidate sandbox responses and callback delivery before production activation.
