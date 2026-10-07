# Payment activation

## Current implemented provider: Paystack
The application reads PAYSTACK_SECRET_KEY from Railway at process startup.
Never commit a key or paste it into public pages. Changing a variable must deploy/restart the web service.

Use approved merchant test credentials first. Configure the matching Paystack dashboard webhook:
https://market.kofadimpex.com/market/payments/paystack/webhook/

The app supplies its callback URL when starting checkout:
https://market.kofadimpex.com/market/payment/return/

Hosted checkout returns to the app; the app verifies the transaction server-side.
Signed successful-payment webhooks also verify reference, amount and currency before settlement.
Temporary verification failures return HTTP 503 for retry. Settlement locks and existing paid-state checks prevent repeat fulfilment.
Missing credentials fail before an attempt is created. Malformed or unsafe checkout responses produce a recoverable error.

Activation verification:
1. Test successful and failed payment paths with provider test tools.
2. Verify webhook delivery and duplicate delivery against the same order.
3. Confirm exactly one sale/stock movement, the right GHS amount, order status and notification draft.
4. Verify cancellation, refund initiation, pending refund and processed refund.
5. Add approved live credentials and verify the provider dashboard settings; do not assume a key alone configures webhooks.
6. Only carry out a real-money test with the owner's explicit transaction authorisation.

Order SMS is queued only when SMS is enabled. SMS needs its own approved sender/provider settings.
WhatsApp needs approved Meta configuration and any required message templates.

## Not implemented / not ready to activate
Hubtel provider selection and staff-initiated mobile-money prompt settlement remain unfinished.
Do not add HUBTEL credentials expecting these features to activate.
The merchant's approved API contract and sandbox verification are still needed.
Meta approval is also pending. Never present a provider as enabled merely because its logo appears.
