# Architecture and invariants

KOFAD is a Python/Django modular monolith backed by PostgreSQL. HTML is rendered on the server; the counter uses a small JavaScript client. Financial writes live in core/services.py. Views perform access checks and presentation. The database provides the final constraints.

## Why this stack

The master plan suggested React/Express/MySQL. The owner authorized technology changes. Django/PostgreSQL reduces the number of independently deployed components and supplies password hashing, CSRF protection, sessions, permissions, migrations, forms and administration. PostgreSQL supports real row locks, checks, atomic transactions and immutable-ledger triggers. Django 5.2 is an LTS release; Railway documents this deployment pattern.

References: https://www.djangoproject.com/download/ and https://docs.railway.com/guides/django

## Posting boundary

1. Require active user, domain permission and location access.
2. Acquire the branch row lock. Every financial posting and daily close takes this lock.
3. Resolve the UUID idempotency key and payload fingerprint. Return the original result for an identical completed request.
4. Reject financial posting after today's closing.
5. Validate catalog modes, integer quantities, payment totals and customer credit.
6. Write the document, immutable line snapshots, inventory movements, payments, allocations and audit evidence in one transaction.
7. Finalize the document before commit. A rollback removes every side effect.

Serializing by branch is a deliberate initial safety tradeoff. Measure busy-counter latency before replacing it with finer lock ordering. It prevents cross-product deadlocks and makes closing atomic with posting.

## Money and inventory

- Decimal money with two fractional digits; invalid, nonfinite, negative and fractional-cent amounts are rejected.
- Browser totals use integer cents. The server always recalculates sale prices.
- Quantities are whole base units. Full packs are a conversion and display layer over the same base-unit ledger, so a 12-unit box can be sold as 2 boxes + 6 loose units and stock remains exactly 330 base units / 27.50 boxes from a 360-unit opening balance. One product supports independently enabled retail/wholesale unit/pack prices.
- Line snapshots preserve product description, price, pack conversion and standard cost at posting time.
- Stock cannot fall below zero. Movements explain each balance change.
- Client-requested discounts and sale-price overrides are accepted only when enabled in company settings. The server recalculates from the current configured product price, enforces hard reduction limits and manager-authority thresholds, requires an override reason, and snapshots list price/final price/discount on the immutable line.
- Configured standard cost is the current costing policy. Receiving does not silently change it.
- Currency is GHS and business timezone is Africa/Accra. Both require an explicit migration/design review before operating in another currency.
- Tax calculation is not enabled. This is a pre-launch policy/integration gap, not a claim of tax compliance.

## Debt and corrections

A sale always resolves to a named customer. Existing customers are searched within the active branch; new customers can be created atomically during checkout and Ghana phone numbers are canonicalized to +233 plus nine national digits. An invoice starts with total minus initial payment. Subsequent allocations reduce outstanding debt. The Debt Desk accepts a partial amount or the full customer balance and allocates one account payment across open invoices oldest-due-first under the branch lock. Collections cannot exceed the customer outstanding amount. Customer credit limits are checked under the same branch lock; zero means no individual customer cap while company credit policy still applies. Credit sales can be disabled, maximum credit days are configurable, and a user with approval authority may exceed a positive customer limit only within the configured maximum override and with a written reason.

Returns refer to original sale lines and use the original selling units and price. Cumulative returned quantity cannot exceed quantity sold. Return value reduces outstanding debt first; any excess is a refund. Returns preserve the original document. Exchanges can be recorded as a return followed by a new sale; an explicit linked exchange workflow is not yet implemented.

Database triggers block updates/deletes on posted documents, lines, payments, allocations, stock movements and audit events. Closing rows allow one independent verification update only. Database owners can alter triggers, so application-level immutability is not protection against a compromised database administrator.

## Stock operations

Request → independent approval → dispatch → receipt.
Approval does not move transfer stock. Dispatch deducts source stock; receipt adds destination stock. Each transition checks the actor's permission at the affected location. Repeated transitions are rejected.

Adjustments require independent approval and a reason. Blind physical counts use frozen stock/movement snapshots and reject stale approvals. Damaged stock uses a separate quarantine workflow: request → independent hold approval → release or write-off. Approved quarantine removes units from sellable stock without pretending they disappeared physically; write-off creates an immutable inventory-loss document at the quarantined cost snapshot. Unsent held carts do not reserve stock.

## Authentication and authorization

Django password hashing and validators, same-origin CSRF-protected requests, secure HTTP-only session cookies, no-store authenticated responses, a restrictive CSP, HTTPS redirects and HSTS are configured.

Five failed password attempts lock the username for 15 minutes. Sign-in uses username and password without mandatory replacement or an authenticator. Security changes and permission membership changes revoke existing sessions. SMS password recovery uses a session-bound, hashed code, ten-minute expiry, five verification attempts and three sends per account per hour. Password and phone changes invalidate pending recovery. Codes are not stored in the customer messaging ledger or audit details.

Django groups define custom permission sets; Access assigns locations. Default group templates are created only once so rerunning bootstrap preserves customization. Administration exposes financial records as read-only.

## Business policy configuration

Company settings now control which payment channels accept new postings, staff/manager discount limits, sale-price reduction limits, credit terms and override ceilings, customer-required and large-sale thresholds, large-expense manager authority, receipt fields and new-reference prefixes. Disabling a payment channel never rewrites historical ledger entries; historical reversals can reproduce the original payment channel so corrections remain faithful to the source record.

These are server-side posting rules, not merely hidden interface controls. Thresholds use the existing `approve_operations` authority as the manager gate. They do not yet create a separate two-person pre-approval queue for discounts, prices, credit or expenses.

## Supplier returns and inventory exceptions

Supplier returns must reference an original purchase line. Pending and approved return quantities cannot exceed the quantity purchased. A different authorized colleague with both approval and finance authority reviews the request. On approval, sellable stock is reduced, the return first reduces outstanding supplier debt through an allocation, and any excess is recorded as an inbound supplier refund on an enabled payment channel. The original purchase remains immutable.

Transfer discrepancy receipts snapshot standard cost. When an independently reviewed discrepancy is confirmed as loss, KOFAD creates an inventory write-off document instead of inventing destination stock or restoring source stock. Quarantine write-offs use the same loss-document pattern.

## Daily closing intelligence

Closing is a reconciliation snapshot, not merely a net cash number. KOFAD separately records gross/net sales, credit created at checkout, customer debt collections, returns/refunds, purchases, supplier payments, expenses and inventory losses. Payment-channel movement is broken down by source. Cash reconciliation adds opening float and explicit other cash in/out to recorded net cash, compares expected against the independently counted amount, records channel variances, then locks financial posting for that business date. A different authorized colleague performs the first verification.

## Reports and communication

Screen and export transaction registers share the same query and location checks. CSV/Excel text is protected against formula injection. PDF and Word exports escape/encode user text through their document libraries. Export audit events record format and date filter.

SMS now uses a durable outbox and an Arkesel provider adapter with consent checks, per-attempt delivery callbacks, explicit outcome states and bounded retries. Live credentials are deployment variables. No live messages were sent during development. WhatsApp remains draft-only. See SMS.md.

## Operating constraints

Lists are capped; reports display 200 rows and export at most 10,000. Proper cursor pagination, indexed global search and larger export jobs are required for high-volume deployment. The application requires online server confirmation; offline financial posting is intentionally unavailable.

## Financial correction requests

Finance requests a reasoned correction; another authorized reviewer accepts or rejects it. Expenses and debt/supplier payments receive an opposite-channel reversal document. Reversing a collection removes its allocation from the live debt calculation while retaining original evidence. Sale voids return all remaining eligible quantities through linked return documents; refunds use the requested channel. Direct record edits remain forbidden.

## Counter recovery

Catalog search fetches new results without navigating away from the cart. The browser keeps product selections and unresolved idempotency keys in per-user, per-location tab session storage. It does not store credentials or card details. An uncertain checkout locks editing until an identical retry recovers the original server result; successful posting clears the stored draft. Signing out clears KOFAD tab drafts. Closing the tab may discard them; staff should use held sales for longer-lived drafts.

## Physical count controls

Count sessions snapshot both quantity and latest movement ID. Entry is blind, submission freezes evidence, and an independent reviewer posts variances under the shared branch lock. Any intervening movement invalidates approval, including net-zero movement pairs. PostgreSQL triggers protect snapshots and completed counts. Validation errors retain the counter's unsaved inputs.

## Transfer receipt evidence

Transfer receipts are immutable records of the quantity actually received. A shortage remains unresolved until a different authorized colleague confirms the remainder arrived or records a loss. Late arrivals add only the remainder; losses add no stock. Operation locks serialize receipt retries and discrepancy resolution, while branch locks coordinate postings with counts and closings.
