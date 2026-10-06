# Acceptance and delivery status

This is a working first implementation, not a claim that all eleven phases of the master plan are production-complete. Automated checks and screenshots are available from GitHub Actions. Passing CI does not eliminate the following launch work.

## Implemented workflows

| Area | Included |
| --- | --- |
| Identity | Password login, lockout, TOTP for staff/superusers, session revocation, custom Django groups, branch assignments |
| Branding | Trade emblem and shared wordmark, distinct responsive login, personalized post-login logo/name welcome, navy/gold/teal operating interface, accessible mobile drawer, task dock and mobile cart shortcut |
| Catalog | Product/SKU/barcode, categories, explicit packed/loose structure, units-per-pack, opening packs + loose units, independent retail/wholesale unit/pack price matrix, archival |
| Inventory | Base-unit stock, immutable movements, nonnegative balances, search/filter workspace, reorder indicators, additive pack/loose quick restock with before/after audit evidence; supplier-accounting receipts remain in Purchasing |
| Sales | Empty-by-default search-first counter, live product/SKU/barcode/category lookup, focused one-product composer, retail/wholesale choice, mixed full-pack + loose-unit quantities, exact base-unit stock, inline existing/new customer selection, Ghana +233 normalization, full/part/credit settlement, held carts, server pricing, configurable payment channels, controlled discounts/price overrides, credit rules, idempotency, receipts |
| Customers | Inline checkout creation/reuse, customer account profile, purchase/activity history, credit limit and available-credit view, configurable overdue grace period, due dates, aging buckets, current/overdue balances, recent collection history, fixed desktop/mobile payment sheet, payment-allocation preview, running statements and account-centric Debt Desk |
| Returns/corrections | Original-line returns, quantity caps, debt reduction then refund; independent expense/payment reversal and remaining-item sale void |
| Purchasing | Supplier contacts, pack-aware receipt, payment splits, supplier debt/payment allocation, original-line supplier returns with independent finance review |
| Operations | Independent stock adjustment review; blind physical count sheets with stale-snapshot protection; transfer request, approval, dispatch, partial receipt and independent discrepancy resolution; damaged-stock quarantine and inventory-loss evidence |
| Finance | Expenses with configurable manager threshold; customer-level partial/full debt payments allocated oldest-due-first; debt reminder timing/grace/cadence controls; daily closing split by sales, credit, collections, refunds, purchases and expenses; opening cash and other cash in/out; counted-vs-expected channel variance; independent verification and closed-day posting lock |
| Reporting | Transaction register, sales/profit by product/mode, exact pack-equivalent inventory, sellable/quarantine valuation, aging, debt summaries, intelligent closing snapshots and inventory losses; CSV/PDF/XLSX/DOCX exports including statements, debts, transfers/operations, closings, supplier returns and quarantine; transaction receipts have exact-size vector A4/80mm/58mm PDF outputs |
| Governance | Financial immutability triggers, audit events, scoped permissions, basic administration |
| Deployment | Live Railway web/worker/PostgreSQL, private setup gate, serialized migrations, verified release branch, dependency lock, remote tests |

## Incomplete launch requirements

- Configure and implement applicable taxes and legally required invoice fields. No tax compliance is implied.
- Validate the implemented independent expense/payment reversal and remaining-item sale-void workflows with KOFAD. Sale void refunds use the explicitly selected channel.
- Manager authority thresholds for expenses, price reductions, discounts and credit-limit overrides are implemented with audit evidence. Add a separate two-person pre-approval queue only if KOFAD requires independent approval rather than manager-authority completion at the point of posting.
- Supplier returns, damaged-goods quarantine and accounting loss documents for confirmed transfer/quarantine losses are implemented. Multiple partial follow-up deliveries on one transfer remain incomplete.
- Add product variants, images, multi-level conversions, batch/serial tracking only as needed; current catalog has one-level packs.
- Arkesel adapter, durable outbox, retry controls, templates, consent-aware debt reminders, management closing/stock drafts and token-authenticated callbacks are implemented. Debt/communication policies can be configured while delivery stays off/draft. Validate the real provider sandbox and approved sender ID before enabling live queueing. WhatsApp Cloud API delivery, durable queueing, signed callbacks, independent event controls and receipt-text notifications are implemented; production activation requires Meta credentials and an approved template (see WHATSAPP.md). Receipt-file attachments remain incomplete.
- Consolidated authorized-branch comparison is implemented, including period sales/cost/expenses and current stock/debt. Extend operational analytics and statutory accounting separately.
- Statements, transfers/operations, closings, supplier returns, quarantine and inventory-loss exports are implemented. Add specialized stock-count and communications exports only if KOFAD's operating process requires them.
- Reports now paginate and reject over-limit queries instead of returning incomplete totals. Extend scoped global-search pagination and asynchronous bulk export jobs. Counter carts and unresolved request keys now survive reloads in the same browser tab.
- App-level signed/checksummed full-system backup, validation, exact-schema restore and guarded business-data reset are implemented. An independent encrypted off-platform backup destination, retention schedule and recurring restore rehearsal are still required for disaster recovery.
- Perform external security review, accessibility audit, production-scale performance tests and owner acceptance.
- Verify live Arkesel delivery after configuring credentials, an approved sender and account recovery numbers.

## Remote verification

The CI suite uses PostgreSQL, not SQLite. It checks migration drift, application checks, business tests, true concurrent oversell protection, immutable ledgers, exports, dependency advisories and Chromium desktop/mobile workflows.

Test scenarios include the plan's 240 → 235 → 199 base-unit example, a 30-pack → 27.5-pack mixed pack/loose sale, inline Ghana customer reuse, customer-level partial/full debt settlement, oldest-due-first allocation, deep closing reconciliation, disabled selling modes, duplicate keys, credit enforcement, over-allocation, return bounds, transfer timing, independent approval, closing locks, CSRF and session revocation.

Browser screenshots are test artifacts with explicitly seeded demo records. They are not KOFAD business data or evidence of a live deployment.

## Configuration still needed from KOFAD

Real branch names, staff assignments, company address/phone, real catalog/prices/opening stock, supplier/customer data, chosen discount/override thresholds, credit limits, receipt policy, tax decisions, payment accounts and messaging provider credentials.

No secrets or live business records belong in this public repository.
