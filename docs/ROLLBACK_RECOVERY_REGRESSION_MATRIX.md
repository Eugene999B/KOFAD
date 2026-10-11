# KOFAD regression prevention after 2026-10-10 promotion

This is a **living verification matrix**, not a claim that every feature is certified. Issue #195 tracks the incident and merges. Production is `railway-release`; do not reset it to an older commit, overwrite the existing PostgreSQL volume, or replace the full repository with a historical snapshot.

## Recovery guards

| Feature | Regression contract |
| --- | --- |
| Customer and staff private login; Google sign-in, email identity | `core.test_staff_login_routes`, `core.test_lost_route_recovery` |
| Staff and customer native app distribution; private PWA | `core.test_native_apps`, `core.test_pwa` |
| Staff ID/photo, QR access and confidential HR fields | `core.test_worker_identity`, `core.test_branding` |
| Staff SMS/WhatsApp recovery and scheduled debt/staff emails | `core.test_communication_worker_recovery`, `core.test_scheduled_automation_recovery` |
| Customer phone ownership verification before first MoMo charge | `marketplace.test_checkout_momo_regression`, `marketplace.tests.DirectMomoPhoneVerificationTests` |
| Card-only Paystack hosted checkout, no wrong-channel settlement | `marketplace.test_card_channel_recovery` |
| Encrypted original Hubtel callback evidence; terminal payment state | `marketplace.test_hubtel.HubtelPaymentTests` |
| Customer-only pickup/delivery code in first paid-order SMS | `marketplace.tests.MarketFulfilmentTests.test_verified_payment_sms_contains_customer_handover_code` |
| Transactional order-created email after commit and consent | `marketplace.test_order_created_email_recovery` |
| Independent Hubtel POS and Market reconciliation worker | `core.test_communication_worker_recovery` |
| Weekly encrypted Google Drive export HTTP contract | `core.test_google_drive_backup` |

`.github/workflows/release-regression-guard.yml` runs a lightweight subset after each push to `railway-release`. The broader `.github/workflows/ci.yml` still runs on PRs and on `main`. Neither workflow must call live providers or charge customers. All synthetic tests use a disposable PostgreSQL container.

## Backup safety

- The backup endpoint emits authenticated, encrypted archives; the database encryption passphrase and HMAC secret must never be committed to GitHub.
- The company-owned Drive folder must contain verified `.kofad.enc` archives. Merely seeing the source code, environment variable names or setup checklist is **not** proof that the Sunday Apps Script trigger is installed.
- Independently confirm the Apps Script owner authorization, Sunday time trigger, successful archive, digest verification, retention, and an **isolated** decryption/restore rehearsal before certifying recoverability.
- Never test recovery by restoring over the live Railway PostgreSQL database. Use an isolated environment with no outbound notifications.

## Deployment and concurrent work precautions

1. Base each narrow recovery branch on the newest `railway-release`, run the focused CI and inspect the full PR diff.
2. Preserve new Android, Firebase, Hubtel, SMS, payroll and staff role developments from other workstreams. Do not merge superseded PRs, run paid provider checks, or force-push production.
3. Verify web and communication-worker Railway deployment statuses separately. Check actual features after promotion; a successful deployment alone is not a pass for transactional email, payments or backups.
4. Treat any broken full-suite regression as a blocker for **final incident certification**. Check whether the failure is application code, stale assertion, CI host restrictions or provider registry throttling before changing business logic.
5. Keep issue #195 open until the complete suite and security-critical staff/customer journeys pass and the off-site backup trigger and isolated restore are verified.
