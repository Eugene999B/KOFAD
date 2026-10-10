# KOFAD intelligent notifications, payroll privacy and credentials

This feature branch intentionally does **not** enable live email or new staff SMS in production.

## Employee payroll

System administrators link an existing staff login to a worker on Workers → Edit worker → Account connection. Each worker may have only one login, and any linked user must be active and belong to the selected location. Linked staff without a company finance/report role may access **only their own approved/locked/reconciled payslips**, via Payroll → My Payslips. Financial exports, other employees' entries, unapproved draft records, editing and authorisation remain forbidden.

## Staff email/SMS automation

Staff accounts have independent toggles for daily closing, weekly review, monthly review, and critical variance email. SMS has its own daily closing and critical-variance toggles, and disabled-by-default deployment switch. Alert eligibility is reassessed before email dispatch against the current active account, role permissions, location, email address and preferences.

A daily closing record produces a deduplicated email digest. Critical cash variance has a separate high-priority email; threshold is the greater of the company closing tolerance and the configured critical amount. Weekly analysis runs Mondays for the preceding Monday–Sunday; monthly analysis runs on the first of each month for the completed previous month. Figures are drawn from submitted daily closing records, with comparative sales, expense, debt collection, cash variance and recommended follow-up. These reports **must not be represented as audited net profit**.

SMS is only one segment, sent on an explicit opt-in and controlled by separate SMS switch; duplicate numbers on the existing management closing contact list are suppressed, and the daily staff recipient cap defaults to 4. SMS credit calculation is therefore predictable. Existing management notifications continue to behave as they did before this branch.

## Customer email

Customer account → Email preferences lets the customer separately enable transactional updates and request **optional** marketing email. Order notifications require an associated paid order and matching currently registered address; no marketing consent is inferred from a purchase. Promotional email requires the customer to click a seven-day, signed, **single-use** address verification link. Requests are deduplicated and previous links become unusable after unsubscribe or address changes. Verified promotional emails can reference items actually saved in the customer's wishlist; no invented discount or guaranteed inventory is advertised, and the campaign cap is at most one send per 21 days per customer.

## Production configuration

Store SMTP credentials in Railway environment variables. Configure the authorised sender and SPF, DKIM, DMARC before enabling mail delivery. Required options:

- `EMAIL_AUTOMATIONS_ENABLED=1` permits generation of staff/customer emails.
- `EMAIL_DELIVERY_ENABLED=1` lets the scheduled worker deliver queued emails.
- `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`, `DEFAULT_FROM_EMAIL` provide SMTP.
- `SMS_STAFF_NOTICES_ENABLED=1` allows new opted-in staff SMS; existing `SMS_ENABLED=1` is also required.
- `SMS_STAFF_DAILY_MAX_RECIPIENTS=4` limits new staff SMS per closing.
- `NOTIFICATION_CRITICAL_VARIANCE_GHS=500` sets the minimum exceptional cash difference in GHS.

The normal `process_sms` Railway worker must already run for scheduled email processing. Ensure only one worker is used for the communications queue where required; event source keys are unique.

Run migrations in staging first: `python manage.py migrate --noinput`. Validate delivery to internally approved test accounts; do not enable customer promotions until email ownership confirmation and opt-out work reliably.

## Deployment readiness

The branch requires CI success, test execution, code review, staging verification (including email sending failure/retries, card-print alignment and role-based access), and an authorised merge/deploy. Never merge a feature branch into main automatically while other workstreams are modifying KOFAD.
