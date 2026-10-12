# KOFAD — Hubtel Direct Receive, Send Money and Send-to-Bank (draft / review only)

This isolated module implements the **new** September 18, 2026 Sales APIs without replacing KOFAD's existing hosted Hubtel checkout or Paystack checkout. It is developed from the Oct 12 `railway-release` snapshot, not from a stale `main` snapshot. No live flags, credentials, IP rules, production deployments or real transfers are changed by this PR. Do not merge until the parallel AI/Telegram deployment is separately reconciled.

## Contract and endpoints

| Flow | Hubtel POST | Independent GET status |
| --- | --- | --- |
| Receive MoMo | `https://rmp.hubtel.com/merchantaccount/merchants/{COLLECTION}/receive/mobilemoney` | `https://api-txnstatus.hubtel.com/transactions/{COLLECTION}/status?clientReference=...` |
| Send MoMo | `https://smp.hubtel.com/api/merchants/{DISBURSEMENT}/send/mobilemoney` | `https://smrsc.hubtel.com/api/merchants/{DISBURSEMENT}/transactions/status?clientReference=...` |
| Send bank | `https://smp.hubtel.com/api/merchants/{DISBURSEMENT}/send/bank/gh/{BANKCODE}` | Same disbursement status API |

Hubtel requires a **static public outbound IP** whitelisted with the assigned Retail Systems Engineer (maximum four IPs per service), the correct Sales API key scopes (including `mobilemoney-receive-direct` for collections) and dedicated Collection/Disbursement account numbers. A standard Railway instance may not provide fixed outbound egress. Obtain approved static egress, **verify the outgoing public IP from the running service**, obtain Hubtel confirmation, and only then set the KOFAD egress confirmation switch. The fixed callback URL is `https://market.kofadimpex.com/market/payments/hubtel-direct/callback/`. A callback is an untrusted hint; only independent status verification can mark a transaction final.

## Disabled-by-default server configuration

- `HUBTEL_API_ID`, `HUBTEL_API_KEY`, `HUBTEL_COLLECTION_ACCOUNT`: current protected Hubtel variables.
- `HUBTEL_DISBURSEMENT_ACCOUNT`: new protected account number, NEVER guess or use the collection account.
- `HUBTEL_DIRECT_EGRESS_CONFIRMED=0`: switch to 1 only after real Hubtel whitelist confirmation.
- `HUBTEL_DIRECT_RECEIVE_ENABLED=0`: direct payment prompts.
- `HUBTEL_DIRECT_SEND_MOMO_ENABLED=0`: wallet payouts.
- `HUBTEL_DIRECT_SEND_BANK_ENABLED=0`: GhIPSS bank payouts.
- `HUBTEL_DIRECT_MAX_PAYOUT_GHS=500.00`: a per-transaction *ceiling*, not a substitute for total daily controls.

Live payout activation requires an owner-approved spending policy, disbursement balance check, tested accounting reconciliation, and authenticated staff MFA. Do not turn on payout switches merely because a branch builds.

## Internal JSON APIs (all same-origin)

- `POST /market/orders/<uuid>/hubtel-direct/` body `{"channel":"mtn-gh"}`, or `vodafone-gh` / `tigo-gh`: customer session required; uses immutable registered **verified** number, order's actual total, and one persisted unique reference. No arbitrary phone/amount in input.
- `GET /market/orders/<uuid>/hubtel-direct/status/`: customer only, returns saved state.
- `POST /market/payments/hubtel-direct/callback/`: public Hubtel callback; no privileged mutation; never marks paid.
- `POST /finance/hubtel-direct/payouts/`: authenticated finance staff with recent MFA create **approval_pending** intent, body e.g. `{"kind":"send_momo","amount":"10.00","destination":"0241234567","recipient_name":"Supplier A","channel":"mtn-gh"}`. For banks use kind `send_bank`, numeric bank account string and documented `bank_code`.
- `POST /finance/hubtel-direct/payouts/<uuid>/approve/`: *different* company manager with recent MFA approves one saved payout and initiates its **single** outbound POST.
- `GET /finance/hubtel-direct/payouts/<uuid>/`: manager-only status.

Creation, approval and receipts require explicit authorized human actions. Do not connect autonomous KOFAD AI or Telegram bot actions to outbound money movement.

## Durability and settlement

Direct collection and outbound transfers record reference and destination **before** external POST. Destination account/phone is encrypted with a separate derived Fernet key; account/phone suffix only is kept for authorized review. The reference is 32 random hexadecimal characters (max Hubtel 36). Missing POST responses remain `submission_unknown` and must **never** be resent blindly. Initial `0001` always means pending, not paid. Reconcile independently after >= 5 minutes using `python manage.py reconcile_hubtel_direct --limit 20` from an approved **existing** worker/scheduler; do not create a new Railway service without a cost review. Staff must reconcile terminal unknowns manually. Collections use KOFAD's existing idempotent `marketplace.services.finalize_payment` for stock/ledger/receipt only after verified amount, reference, currency and Hubtel ID. Payouts are tracked as provider transactions; **a full bank-ledger reconciliation workflow and daily limit controls remain launch blockers.** A provider success must not automatically create an expense, refund or debt adjustment.

## Acceptance checklist (not performed by creating this PR)

1. Inspect migration plan, run Django `check`, `makemigrations --check --dry-run`, Hubtel Direct isolated tests and complete existing checkout/AI/Telegram and finance regression suites against the latest integration tree.
2. Ensure `railway-release` and this branch comparison does not revert any parallel AI/Telegram features, and no unrelated file deletion or migration conflict exists.
3. Verify whitelisted outbound egress IP and callback deployment path, Hubtel API scopes, collection/disbursement account IDs, callback ownership, correct credential domain and retail engineer approval.
4. Run only pre-approved low-value provider UAT with merchant and customer participation: pending, failed, wrong PIN, timeout, duplicate callback, wrong amount, delayed callback, bank invalid account, disbursement low balance and cancellation.
5. Link existing worker invocation, owner-specific payout accounting, ledger reconciliation, aggregate daily budgets/limits, approval dashboard and redacted monitoring before **any** live outbound payout.
6. Separate feature flags and a tested rollback path must permit instant shutdown **without reverting** the rest of KOFAD. The new app should not be made production-active until all gates above pass.

**No live transaction tests and no production deployment in this change.**
