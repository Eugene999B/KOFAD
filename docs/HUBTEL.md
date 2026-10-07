# Hubtel Online Checkout

Scope agreed with Hubtel on 7 October 2026: Online Checkout only, using its public transaction-status endpoint without static outbound-IP whitelisting. Railway remains on Hobby. Direct MoMo prompts, transfers and other APIs are not included.

## Configuration

Set HUBTEL_API_ID (Sales API ID), HUBTEL_API_KEY (Sales API key) and HUBTEL_COLLECTION_ACCOUNT in kofad-web. Reference the same variables from kofad-sms, which runs the payment reconciliation worker. No secrets belong in GitHub, browser JavaScript, PDFs or logs.

HUBTEL_CHECKOUT_ENABLED defaults to 0. Enable only for agreed testing/launch. Company administrators select Hubtel or Paystack in Market & Delivery settings. Existing attempts keep their saved provider. Without an explicit setting, a configured Hubtel account is selected, otherwise Paystack.

## Interfaces

- POST https://payproxyapi.hubtel.com/items/initiate
- GET https://rmsc.hubtel.com/v1/merchantaccount/merchants/{COLLECTION_ACCOUNT_NUMBER}/transactions/status?clientReference={clientReference}
- Authorization: Basic base64(API_ID:API_KEY), server only.
- Callback: https://market.kofadimpex.com/market/payments/hubtel/callback/
- Return/cancellation: https://market.kofadimpex.com/market/payments/hubtel/return/?reference={saved-reference}

Source: merchant developer portal Online Checkout and Authentication pages, reviewed 7 October 2026. The public status URL was supplied directly by the Hubtel representative. Its real response must be verified during integration testing; unexpected schemas fail closed. The published status schema permits a null currencyCode. Only this GHS collection account and GHS orders are supported.

## Payment integrity

Random 32-character references identify saved attempts. Initialization intent is committed before network I/O. Missing/malformed responses retain an unresolved attempt instead of creating another payment request. Redirects only accept HTTPS pay.hubtel.com. Callbacks never prove payment and do not trigger a charge. Independent status checks validate the saved reference, amount and transaction ID before using the existing transactional stock/ledger/receipt workflow.

The worker checks unresolved attempts after five minutes, with persisted leases, bounded batches and a 60-check limit. Unresolved or mismatched attempts require staff reconciliation; do not request a second payment or release goods. A checkout return never displays a false paid result. Paystack webhooks cannot settle Hubtel attempts. Provider changes cannot reroute existing attempts. Hubtel returns cannot invoke Paystack refunds.

## UAT evidence still required

Automated tests use mocked provider responses and are not live UAT evidence. Test hosted checkout on mobile and desktop; collect redacted actual callbacks and a real status-check response; verify success, cancellation, delayed response, duplicate notification, wrong amount and stock/ledger/receipt behavior. Confirm the public response schema and accepted test-payment procedure with Hubtel. Do not make live test charges without the user's approved amount/payee and participation. Arrange Hubtel UAT before public launch.

Inspect payment_attempt.verification_summary for the redacted status fields; no customer phone, payment PIN or API secret is saved there. Share actual evidence only after testing. Hubtel refunds currently require an approved manual process; no automatic refund API is claimed.
