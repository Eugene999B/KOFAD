# Paystack live integration

Customer checkout follows Settings → Online payments → selected provider. Existing attempts retain their provider until settled. Secrets are server-side Railway variables on both web and worker; never commit them.

- PAYSTACK_SECRET_KEY: KOFAD live secret key.
- PAYSTACK_POS_MOMO_ENABLED=1: staff direct MoMo approval.
- PAYSTACK_CUSTOMER_MOMO_ENABLED=1: customer direct MoMo approval.
- Live webhook: https://market.kofadimpex.com/market/payments/paystack/webhook/
- Live callback: https://market.kofadimpex.com/market/payment/return/

Customers choosing Paystack can request MTN MoMo, AT Money or Telecel approval directly, or open hosted checkout for card and other available channels. A MoMo PIN is entered only on the customer's handset, never on KOFAD. Recipient and payer numbers may differ.

Amounts come from saved orders. The request intent is saved before provider calls. Double submissions reuse the original intent. Uncertain results cannot create another charge. Signed webhooks and background status checks independently verify the reference, amount and currency before the order posts to the ledger. Direct MoMo also requires its verified channel and transaction ID. Failed status is only reported after provider verification. Late/unresolved payments remain under review to prevent unsafe repeat charges.

Confirmed orders use existing receipt SMS and fulfilment tracking. Staff counter sales use the existing verified Paystack MoMo pipeline. Hubtel's working checkout is retained.

Confirm payout destinations separately in Paystack Settings → Accounts. KOFAD's receiving-account reference fields do not change provider payouts. Real approval, paid webhook, SMS and settlement must be checked with an explicitly authorized live payment.

Official contract:
https://paystack.com/docs/payments/payment-channels/#mobile-money
https://paystack.com/docs/payments/webhooks/
https://paystack.com/docs/api/charge/
