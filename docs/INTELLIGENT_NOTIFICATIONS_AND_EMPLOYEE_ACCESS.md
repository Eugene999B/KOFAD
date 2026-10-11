# KOFAD intelligent notifications, payroll privacy and credentials

Production email uses KOFAD's existing Brevo HTTPS gateway, shared free daily quota and independent notification kill switches. Staff/customer permissions are checked before every delivery; SMS uses its own credit-saving recipient cap.

## Employee payroll

System administrators link an existing staff login to a worker on Workers → Edit worker → Account connection. Each worker may have only one login, and any linked user must be active and belong to the selected location. Linked staff without a company finance/report role may access **only their own approved/locked/reconciled payslips**, via Payroll → My Payslips. Financial exports, other employees' entries, unapproved draft records, editing and authorisation remain forbidden.

## Staff email/SMS automation

Staff accounts have independent toggles for daily closing, weekly review, monthly review, and critical variance email. Sensitive staff emails require a currently verified email identity with account notifications enabled. The deployment migration seeds those four email preferences only for existing active superusers who have verified and opted into mailbox notifications. SMS has its own daily closing and critical-variance toggles, and disabled-by-default deployment switch. Alert eligibility is reassessed before email dispatch against the current active account, role permissions, location, email address and preferences.

A daily closing record produces a deduplicated email digest. Critical cash variance has a separate high-priority email; threshold is the greater of the company closing tolerance and the configured critical amount. Weekly analysis runs Mondays for the preceding Monday–Sunday; monthly analysis runs on the first of each month for the completed previous month. Figures are drawn from submitted daily closing records, with comparative sales, expense, debt collection, cash variance and recommended follow-up. These reports **must not be represented as audited net profit**.

SMS is only one segment, sent on an explicit opt-in and controlled by separate SMS switch; duplicate numbers on the existing management closing contact list are suppressed, and the daily staff recipient cap defaults to 4. SMS credit calculation is therefore predictable. Existing management notifications continue to behave as they did before this branch.

## Customer email

Customer account → Email preferences lets the customer separately enable transactional updates and request **optional** marketing email. Order notifications require an associated paid order and matching currently registered address; no marketing consent is inferred from a purchase. Promotional email requires the customer to click a seven-day, signed, **single-use** address verification link. Requests are deduplicated and previous links become unusable after unsubscribe or address changes. Verified promotional emails can reference items actually saved in the customer's wishlist; no invented discount or guaranteed inventory is advertised, and the campaign cap is at most one send per 21 days per customer.

## Production configuration

KOFAD's existing **Brevo HTTPS transactional email** handles this feature; Railway Hobby SMTP ports are not used. Sender accounts and domain authentication must be verified before activation. Maintain these variables on both `kofad-web` and the existing `kofad-sms` communication worker:

- `KOFAD_EMAIL_ENABLED=1`, `KOFAD_EMAIL_PROVIDER=brevo` enable the verified Brevo sender.
- `KOFAD_EMAIL_NOTIFICATIONS_ENABLED=1` permits delivery of existing verified-account notification notices.
- `KOFAD_EMAIL_CENTER_ENABLED=1` enables the normal staff email-centre business workflow.
- `EMAIL_AUTOMATIONS_ENABLED=1` permits intelligent staff/customer report generation.
- `EMAIL_DELIVERY_ENABLED=1` permits the same worker to deliver these notices.
- Existing `KOFAD_BREVO_API_KEY`, `KOFAD_BREVO_SECURITY_FROM_EMAIL`, `KOFAD_BREVO_TRANSACTION_FROM_EMAIL`, and `KOFAD_BREVO_REGISTERED_SENDERS` remain unchanged.
- All three outboxes use the existing `kofad-sms` worker, not an extra Railway service. The Brevo integration enforces a hard maximum of 300 attempted recipient emails per day shared across services; an interrupted HTTPS response is quarantined instead of blindly resent.
- `SMS_STAFF_NOTICES_ENABLED=1` permits **only explicitly enabled** staff SMS subscriptions, in addition to the existing `SMS_ENABLED` and SMS provider settings.
- `SMS_STAFF_DAILY_MAX_RECIPIENTS=2` caps extra staff SMS to two recipients per location/closing; existing management closing contacts are deduplicated to reduce costs.
- `NOTIFICATION_CRITICAL_VARIANCE_GHS=500` is the default critical cash variance threshold.

**Consent:** customer marketing must be double-confirmed through a signed one-time link; transaction messages require the current verified customer email address. Staff reports require currently verified opted-in staff email identities and role/location entitlement. Turning off account notifications or revoking a mailbox cancels queued messages at dispatch.

Run `python manage.py migrate --noinput` with deployment. Only enable paid SMS on a verified, credit-funded SMS provider; never disable `SMS_SANDBOX` as a side effect of activating these new switches.

## Deployment readiness

Changes require migration, CI checks, access-control tests, a successful production deployment, and observation of delivery outcomes. Any uncertain email provider acknowledgment is quarantined for manual reconciliation rather than automatically resent.
