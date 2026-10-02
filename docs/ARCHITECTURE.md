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
- Quantities are whole base units. One product supports independently enabled retail/wholesale unit/pack prices.
- Line snapshots preserve product description, price, pack conversion and standard cost at posting time.
- Stock cannot fall below zero. Movements explain each balance change.
- No client-provided sale price overrides or discounts are accepted.
- Configured standard cost is the current costing policy. Receiving does not silently change it.
- Currency is GHS and business timezone is Africa/Accra. Both require an explicit migration/design review before operating in another currency.
- Tax calculation is not enabled. This is a pre-launch policy/integration gap, not a claim of tax compliance.

## Debt and corrections

An invoice starts with total minus initial payment. Subsequent allocations reduce outstanding debt. Collections cannot exceed the outstanding amount. Customer credit limits are checked under the same branch lock.

Returns refer to original sale lines and use the original selling units and price. Cumulative returned quantity cannot exceed quantity sold. Return value reduces outstanding debt first; any excess is a refund. Returns preserve the original document. Exchanges can be recorded as a return followed by a new sale; an explicit linked exchange workflow is not yet implemented.

Database triggers block updates/deletes on posted documents, lines, payments, allocations, stock movements and audit events. Closing rows allow one independent verification update only. Database owners can alter triggers, so application-level immutability is not protection against a compromised database administrator.

## Stock operations

Request → independent approval → dispatch → receipt.
Approval does not move transfer stock. Dispatch deducts source stock; receipt adds destination stock. Each transition checks the actor's permission at the affected location. Repeated transitions are rejected.

Adjustments require independent approval and a reason. A count discrepancy can be posted as an adjustment, but structured count sessions, blind counts and frozen count snapshots remain future work. Unsent held carts do not reserve stock.

## Authentication and authorization

Django password hashing and validators, same-origin CSRF-protected requests, secure HTTP-only session cookies, no-store authenticated responses, a restrictive CSP, HTTPS redirects and HSTS are configured.

Five failed password attempts lock the username for 15 minutes. Staff/superuser and company-management accounts must enroll a TOTP authenticator. Other enrolled users must also verify. TOTP steps cannot be reused. Security changes and permission membership changes revoke existing sessions. Operator MFA reset requires host access and records a reason.

Django groups define custom permission sets; Access assigns locations. Default group templates are created only once so rerunning bootstrap preserves customization. Administration exposes financial records as read-only.

## Reports and communication

Screen and export transaction registers share the same query and location checks. CSV/Excel text is protected against formula injection. PDF and Word exports escape/encode user text through their document libraries. Export audit events record format and date filter.

SMS now uses a durable outbox and an Arkesel provider adapter with consent checks, per-attempt delivery callbacks, explicit outcome states and bounded retries. Live credentials are deployment variables. No live messages were sent during development. WhatsApp remains draft-only. See SMS.md.

## Operating constraints

Lists are capped; reports display 200 rows and export at most 10,000. Proper cursor pagination, indexed global search and larger export jobs are required for high-volume deployment. The application requires online server confirmation; offline financial posting is intentionally unavailable.

## Financial correction requests

Finance requests a reasoned correction; another authorized reviewer accepts or rejects it. Expenses and debt/supplier payments receive an opposite-channel reversal document. Reversing a collection removes its allocation from the live debt calculation while retaining original evidence. Sale voids return all remaining eligible quantities through linked return documents; refunds use the requested channel. Direct record edits remain forbidden.

## Counter recovery

Catalog search fetches new results without navigating away from the cart. The browser keeps product selections and unresolved idempotency keys in per-user, per-location tab session storage. It does not store credentials or card details. An uncertain checkout locks editing until an identical retry recovers the original server result; successful posting clears the stored draft. Signing out clears KOFAD tab drafts. Closing the tab may discard them; staff should use held sales for longer-lived drafts.
