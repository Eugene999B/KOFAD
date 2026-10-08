# Payment and WhatsApp security review — 8 October 2026

Baseline: railway-release a29fab8eb12e8377c22b12ae7901493f1e9609b6, including the recent Hubtel live response mapping and Paystack POS work. No provider credentials, payout destinations or live payment flags are changed.

## Remediations

- Held-cart JSON cannot manufacture internal Paystack MoMo records.
- POS request replay checks owner, branch and original sale/recipient; verification checks exact minor units, currency, channel, reference and transaction identity.
- Verification has a database claim; slower responses cannot overwrite successful or quarantined state.
- A changed product price cannot post a different sale total after the customer paid.
- Uncertain Paystack charge/checkout responses remain uncertain, preventing unsafe repeat requests.
- Checkout initialization is serialized per order and unresolved attempts retain their provider.
- Paystack background reconciliation survives browser closure; completed POS records do not starve pending records.
- Anonymous Paystack return pages no longer disclose order payment state.
- Paystack webhook payloads are size-limited. WhatsApp webhook collections are type-checked, configured account/phone IDs are scoped, and outbound Meta requests cannot follow redirects.
- Bot replies are durable, bounded, deduplicated, opt-out aware and restricted to the 24-hour customer window. Backup restore quarantines queued assistant replies.

## Bot trust boundary

The bot uses an explicit command router, not an LLM with database tools. Prompt injection, role claims, SQL text and payment-approval instructions cannot execute actions. Public catalog lookup uses Django ORM parameters. Orders, receipts, account changes and staff functions link to authenticated pages with existing authorization. WhatsApp phone ownership is not treated as proof of ownership of a website account. HUMAN creates a support enquiry; staff must verify identity before disclosing account information.

## Verification and limits

Regression tests cover forged carts, cross-user replay, changed requests, stale response races, payment leases, fractional amounts, mismatched references, oversized and malformed webhooks, prompt injection, stale/replayed WhatsApp events, opt-out, expiry, handoff and uncertain delivery.

This is a targeted code review and regression exercise, not a guarantee that every vulnerability is eliminated. Live destructive probes, customer charges and bulk messages are excluded. Meta production messaging still requires approval, number registration, credentials, publication and a real delivery test. Paystack activation/credentials and provider UAT are external requirements.

An owner account still using a publicly known initial password remains a significant risk. Password rotation must be completed by the owner; this change does not silently replace credentials or lock the owner out.

References:
- https://paystack.com/docs/payments/webhooks/
- https://paystack.com/docs/api/transaction/
- https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/overview/
