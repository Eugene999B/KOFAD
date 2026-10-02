# Acceptance and delivery status

This is a working first implementation, not a claim that all eleven phases of the master plan are production-complete. Automated checks and screenshots are available from GitHub Actions. Passing CI does not eliminate the following launch work.

## Implemented workflows

| Area | Included |
| --- | --- |
| Identity | Password login, lockout, TOTP for staff/superusers, session revocation, custom Django groups, branch assignments |
| Branding | Original SVG mark/wordmark, responsive forest/ivory interface, keyboard focus and counter shortcuts |
| Catalog | Product/SKU/barcode, categories, units/packs, independent retail/wholesale price matrix, archival |
| Inventory | Base-unit stock, immutable movements, nonnegative balances, reorder indicators |
| Sales | Mixed-mode cart, held carts, server pricing, cash/MoMo/bank/card splits, credit, idempotency, receipts |
| Customers | Quick contacts, credit limit, due date, collections, current balances, running statements |
| Returns/corrections | Original-line returns, quantity caps, debt reduction then refund; independent expense/payment reversal and remaining-item sale void |
| Purchasing | Supplier contacts, pack-aware receipt, payment splits, supplier debt/payment allocation |
| Operations | Independent stock adjustment review; blind physical count sheets with stale-snapshot protection; transfer request, approval, dispatch and receipt |
| Finance | Expenses, channel movement reconciliation, independent closing verification, closed-day posting lock |
| Reporting | Transaction register, sales/profit by product/mode, inventory valuation and aging; CSV/PDF/XLSX/DOCX exports; printable receipts/statements |
| Governance | Financial immutability triggers, audit events, scoped permissions, basic administration |
| Deployment | Container, Railway configuration, PostgreSQL migrations, dependency lock, remote tests |

## Incomplete launch requirements

- Configure and implement applicable taxes and legally required invoice fields. No tax compliance is implied.
- Validate the implemented independent expense/payment reversal and remaining-item sale-void workflows with KOFAD. Sale void refunds use the explicitly selected channel.
- Add approval thresholds for expenses, price overrides, discounts and credit overrides. These overrides are currently unavailable.
- Extend damaged stock/transfer discrepancy handling and supplier returns. Structured blind stock-count sessions and independent variance posting are implemented.
- Add product variants, images, multi-level conversions, batch/serial tracking only as needed; current catalog has one-level packs.
- Arkesel adapter, durable outbox, retry controls, templates and token-authenticated callbacks are implemented. Validate the real provider sandbox and approved sender ID, then enable the worker. WhatsApp and receipt-file attachments remain incomplete.
- Extend the implemented sales/gross-profit, receivables-aging and inventory-valuation reports with consolidated branch comparisons and operational analytics.
- Add complete export coverage for statements, transfers, closings and other report families.
- Extend implemented scoped global search with scalable pagination and export jobs. Counter carts and unresolved request keys now survive reloads in the same browser tab.
- Configure, automate and rehearse encrypted independent backup/restore with manifests and retention.
- Perform external security review, accessibility audit, production-scale performance tests and owner acceptance.
- Provide self-service MFA recovery codes and sensitive-action reauthentication if required by the owner's security policy.

## Remote verification

The CI suite uses PostgreSQL, not SQLite. It checks migration drift, application checks, business tests, true concurrent oversell protection, immutable ledgers, exports, dependency advisories and Chromium desktop/mobile workflows.

Test scenarios include the plan's 240 → 235 → 199 base-unit example, disabled selling modes, duplicate keys, credit enforcement, over-allocation, return bounds, transfer timing, independent approval, closing locks, CSRF and session revocation.

Browser screenshots are test artifacts with explicitly seeded demo records. They are not KOFAD business data or evidence of a live deployment.

## Configuration still needed from KOFAD

Real branch names, staff assignments, company address/phone, real catalog/prices/opening stock, supplier/customer data, credit limits, receipt policy, tax decisions, payment accounts and messaging provider credentials.

No secrets or live business records belong in this public repository.
