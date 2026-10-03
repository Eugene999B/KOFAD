# CHALIN03 comparison and KOFAD direction

Reviewed the CHALIN03 repository's README, complete source path inventory, Spare Parts help topics, SMS routes, SMS submission adapter and reliability service. This is a source/design comparison; it is not a complete independent audit of CHALIN03's running production system. No CHALIN03 code, settings, credentials or live records were modified.

CHALIN03 covers Spare Parts, Mining, Equipment Sales & Hire, Group Executive control, workforce/payroll, documents, signatures, accounting intelligence and recovery. Its broad documented feature coverage provides a useful reference. Mining, hire and payroll are separate businesses and are not silently added to KOFAD's retail/wholesale scope.

| CHALIN03 capability | KOFAD treatment |
| --- | --- |
| Scoped stores/sites and permission checks | Branch assignments and service-level permissions already enforced |
| Financial corrections with original evidence | Independent expense/payment reversal and sale void workflows already implemented |
| Transfer approval, dispatch and receive | Separate state transitions and movement timing already implemented |
| Independent closing verification | Implemented and expanded with opening cash, other cash in/out, source-by-channel reconciliation, credit-created vs cash-received separation and immutable verification evidence |
| Arkesel, optional future provider | Arkesel adapter plus extensible registry; no ambiguous automatic failover |
| Accepted/delivered/unknown distinctions | Explicit statuses and attempt-scoped, token-authenticated callbacks |
| Message credit estimates | GSM extension accounting and UTF-16 code-unit handling, including emoji |
| SMS retry protection | Durable outbox, per-attempt records, bounded throttling retries, unknown-result hold |
| Customer reuse and debt desk | Retains the useful account-centric idea but KOFAD uses its own workspace: aging buckets, credit usage, recent collections, open-receipt detail, allocation preview, checkout-time Ghana identity normalization, automatic reuse/creation and immutable oldest-due-first multi-invoice allocations |
| Receipt, debt and payment messages | KOFAD separates Debt Settings (due/overdue policy, cadence, consolidated account reminder) from Communication Settings (sale/payment/closing/stock events, management recipients) and the outbox; automatic customer messages remain consent-aware |
| Signed recovery manifests and restore drills | KOFAD now has signed/checksummed app-level full backup validation, exact-schema restore and guarded business-data reset; independent off-platform retention and recurring restore rehearsal remain required |
| Detailed workforce and document lifecycle | Future scope; requires a KOFAD-specific business case |
| Full accounting intelligence and consolidated executive reports | Basic profit/valuation/aging exists; broader analytics remain planned |

## Improvements implemented in this iteration

- The sales counter is deliberately search-first instead of showing the full catalog; adding a product returns focus to search while preserving the cart.
- Debt is presented as a customer account workspace; payment entry stays in a fixed desktop dialog/mobile bottom sheet instead of expanding the page, and overdue timing is configurable independently from sale credit-term limits.
- Inventory Quick Restock adds to the current balance and records before/after evidence, while supplier-accounting purchases remain a separate workflow.
- Successful login now transitions through a short KOFAD-branded personalized welcome before the user's permission-scoped workspace.
- Receipts now have server-generated vector A4, 80mm and 58mm PDF formats so thermal output does not depend on scaling an A4 browser page.
- Public business phones and location are configured separately from customer/account numbers and print as business identity on receipts.
- SMS submission runs outside the cashier's request in a durable worker.
- Per-attempt callback tokens reduce the impact of a shared callback credential.
- Unknown provider outcomes cannot silently switch gateways and duplicate billing.
- Sandbox evidence cannot be mistaken for real delivery.
- UTF-16 estimates count non-BMP characters correctly.
- The owner requested direct administrator sign-in. Password change is optional in My account; SMS recovery is available after live delivery and per-account numbers are configured.

These are concrete design improvements, not a claim that the whole KOFAD platform already exceeds CHALIN03 in feature breadth. The remaining acceptance work stays visible in ACCEPTANCE.md.
