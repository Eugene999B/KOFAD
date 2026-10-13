# KOFAD Hubtel Finance: Release and UAT Gates (13 October 2026)

**Development status:** PR #250 is a non-production finance update. Do not confuse
adding Python modules with an operational, end-to-end financial integration.
Do not merge or activate until the relevant tests and merchant-authorized UAT pass.

## Deployment invariant
All Hubtel payment, verification, mandate, refund, and transfer requests must
**originate from the `kofad-web` service**, whose three static outbound addresses
(162.220.232.250, 152.55.176.240, 152.55.177.193) were submitted for Hubtel
whitelisting. The `kofad-sms` worker may call the authenticated internal web
reconciliation endpoint; it must never reach Hubtel directly. A provider 403
or timeout is not proof that money did not move. Never re-POST ambiguous intents.

Existing KOFAD AI, Telegram, POS, checkout, payroll and customer features must
remain intact. Compare the latest `railway-release` commit, run migrations and
regression checks, and deploy only a known tested descendant of the current
production commit. No force pushes/resets.

## Direct Debit customer debt
1. Staff records actual outstanding sale debt, agreed wallet and schedule; the
   plan cannot exceed the recorded debt and has a hard lifetime GHS ceiling.
2. Customer reviews full terms and verifies **ownership** of the mobile number
   through KOFAD's SMS code. Only then initiate Hubtel preapproval.
3. Customer separately completes Hubtel USSD/OTP. Verify `APPROVED` by
   Hubtel's authenticated status endpoint; callback text alone is not authority.
4. Scheduler prepares only amounts due under the signed plan. Recheck consent,
   remaining debt, ceiling, timing and duplicate references on **kofad-web**.
5. Never repeatedly charge shrinking amounts to discover/drain wallet balance.
   If a result remains unknown/unpaid, do not issue another payment attempt
   until provider status and finance review establish a safe next action.
   Carry unpaid contractual installments forward only when failure is
   conclusively established and permitted by the customer's original terms.
6. Customers may cancel with wallet OTP on the KOFAD authorization page.
   Immediately mark locally `cancel_pending` to prevent new charges.
   Retry unresolved Hubtel cancellations no faster than 15 minutes, and retain
   `cancel_pending` until provider confirms cancellation.
7. Automatically revoke authorization when the account has been fully
   settled, including when it was paid by a different KOFAD payment method.
   Round-robin scans prevent starvation when there are many active accounts.

**Supported direct debit channels documented by Hubtel:** MTN and Telecel,
not AirtelTigo. Hubtel preapproval is wallet/merchant-level, so never maintain
two active debt mandates for the same wallet.

## Payroll
- Only approved/locked payroll periods; send net salary **after all deductions**
  and earlier payments.
- The recipient must choose MoMo or an available Ghana bank; verify the
  account-holder name via Hubtel and store destination encrypted.
- Reverify stale beneficiary profiles before initiating a new pay period.
- Prepare an immutable batch, then require a different authorized manager to
  release it. The creator must not approve their own batch.
- Reserve company-wide daily payout ceiling and per-transaction limits;
  persist unique references before sending. A network timeout never permits
  an automatic replay.
- Independently reconcile each confirmed transfer before posting a payroll
  payment. An uncertain or failed transfer must remain visible to finance
  instead of being quietly marked paid.
- Outgoing funds require `HUBTEL_DISBURSEMENT_ACCOUNT` (different from
  collection account), disbursement balance, and the correct Hubtel scopes.

## Refund and returns
- The supplied Hubtel Refund API is **mobile-money original-order refunds only**
  and is documented for transactions within **45 days**. Older requests need
  Hubtel manual handling (business@hubtel.com).
- A refund cannot be released merely because someone supplied an order ID.
  It must match a successful recorded KOFAD Hubtel payment and an approved
  return; verify that the provider refund is for exactly the eligible amount.
- Hubtel's documented Refund API operates on an **order ID**, not an arbitrary
  subset of individual items. Partial-item returns must not trigger a full
  order refund. Offer an approved replacement/exchange or manual reviewed
  settlement until a provider-supported partial refund mechanism is proven.
- Require distinct requester and approver, a one-time durable submission,
  callback evidence and independent finance reconciliation. A success-looking
  unauthenticated callback cannot change the refund ledger.
- The current `submit_refund` API helper and model are **not a complete refund
  release workflow**. Do **not** enable live refunds until the approved-case
  endpoints, callback handling, identity binding and ledger reconciliation
  are implemented and UAT-tested.

## Launch checklist
- [ ] Merge up-to-date feature code into current release only after CI passes:
      Django checks/migrations, finance/Hubtel tests, POS/order/returns, AI,
      Telegram and backup workflow health.
- [ ] Confirm current production static egress on `kofad-web` and Hubtel
      enabled scopes using safe, merchant-approved checks.
- [ ] Confirm collection and disbursement account numbers and separately
      approved payout limits; keep actual credentials in Railway secrets.
- [ ] UAT opt-in SMS + Hubtel preapproval on a controlled consenting wallet;
      test cancel, repeated cancel, no charge after cancel, and full payment.
- [ ] UAT each intended collection, refund and salary transfer with a small
      specifically authorized merchant transaction. Verify server-side
      transaction reference, provider status, receipts and accounting.
- [ ] Confirm missing/duplicate callbacks, timeouts and unknown statuses
      cannot cause double charges, double salary payments or refunds.
- [ ] Turn on **one feature flag at a time**; monitor audit logs and allow
      immediate feature disable without rolling back application code.

## Environment feature switches (default OFF)
- `HUBTEL_DEBT_PREAPPROVAL_ENABLED`
- `HUBTEL_DEBT_AUTO_DEBIT_ENABLED`
- `HUBTEL_PAYROLL_DISBURSEMENTS_ENABLED`
- `HUBTEL_REFUNDS_ENABLED`
- `HUBTEL_DIRECT_SEND_MOMO_ENABLED`
- `HUBTEL_DIRECT_SEND_BANK_ENABLED`
- `HUBTEL_DIRECT_EGRESS_CONFIRMED` (enables only confirmed network origin)
- `HUBTEL_DIRECT_RECEIVE_ENABLED` (separate existing collection gate)

Do not set all flags to 1 as a shortcut; these switches are not test evidence.
