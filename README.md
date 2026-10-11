# KOFAD IMPEX ENTERPRISE

A unified retail and wholesale operating system for KOFAD. One catalog, pack-aware stock, a fast counter, and traceable financial records.

## Technology decision
Python 3.12, Django 5.2 LTS, PostgreSQL 16, server-rendered HTML and focused vanilla JavaScript. A modular monolith keeps transactional boundaries explicit and avoids running separate frontend and API infrastructure. PostgreSQL row locks and database constraints protect concurrent stock and payment operations. Railway is the intended deployment target.

## Product direction
A connected commerce identity: the official KOFAD navy, charcoal and champagne-gold identity, grouped task navigation, mobile checkout shortcuts and guided branch setup. All dashboard figures must come from persisted records; there are no fabricated business metrics.

## Source of requirements
KOFAD IMPEX ENTERPRISE Master Software Plan, version 1.0, 2 October 2026, supplied by the owner. Treat its sample prices and metrics as illustrations, not production records. Business policy and provider credentials must be configured before launch.

## Delivery discipline
Implementation and commits are made directly in this GitHub repository. GitHub Actions is the remote verification environment. No local checkout is required.

The implementation, operational guide, acceptance matrix and launch limitations will be added alongside the application. A successful build is not a substitute for a production security review, restore rehearsal and owner acceptance of tax, credit and approval policies.

## License
Proprietary. Copyright KOFAD IMPEX ENTERPRISE. No license to use, distribute or sublicense is granted by public repository visibility.

## Application

The repository now contains a Django/PostgreSQL implementation of the core operating workspace:

- Search-first sales counter: no catalog wall on entry, live name/SKU/barcode/category search, focused product composer, retail/wholesale selection, full-pack plus loose-unit selling, exact base-unit stock, held sales, inline customer search/creation, configurable payment channels, controlled discounts/price overrides and full/part/credit settlement.
- Products, additive quick restock with before/after audit evidence, supplier purchasing, stock movements, independent adjustment approvals, dispatch/receive transfers, damaged-stock quarantine and inventory-loss evidence.
- Customer/supplier records, Ghana-normalized customer phones, customer account profiles, an account-centric Debt Desk with configurable overdue grace periods, aging buckets, credit usage, recent payment history, fixed desktop/mobile payment sheet, allocation preview and partial/full oldest-due-first settlement, purchase receiving, customer returns, independently reviewed supplier returns, expenses and independently approved corrections.
- Daily closing intelligence separating sales, credit created, debt collections, refunds, expenses, purchases and payment channels, with opening cash, other cash in/out, counted-vs-expected variance, independent verification and a posting lock after closing.
- Receipts with separate public business contacts/location, sharp vector A4/80mm/58mm PDFs, statements, debt summaries, transaction/profit/stock/aging/closing reports and PDF/Excel/Word/CSV exports.
- Custom roles, location access, privileged TOTP, session revocation and audit evidence.
- Original identity, a distinct login experience, personalized post-login welcome transition, consistent responsive desktop/mobile workspaces and light/dark themes.

**Status:** pre-production implementation. The entire master plan is not complete. Manager authority thresholds for expenses, discounts, price reductions and credit overrides are now configurable and server-enforced. Tax handling, independent two-person override approval queues, WhatsApp business-account activation, advanced catalog features and operational recovery work remain. Read the [acceptance matrix](docs/ACCEPTANCE.md) before using real money or business data.

## Verification

[![Verify KOFAD](https://github.com/Eugene999B/KOFAD/actions/workflows/ci.yml/badge.svg)](https://github.com/Eugene999B/KOFAD/actions/workflows/ci.yml)

GitHub Actions runs PostgreSQL-backed business and concurrency tests, migration checks, dependency auditing, and Chromium desktop/mobile navigation, direct administrator login, account settings, cart search and lost-response checkout recovery checks. The production Docker image is also built and health-tested. Browser screenshots are available in the run's browser-evidence artifact. Demo records are explicitly labeled and only permitted in DEBUG environments.

## Deployment and operations

- [Railway deployment and recovery](docs/RAILWAY.md)
- [Architecture and integrity rules](docs/ARCHITECTURE.md)
- [Acceptance and remaining launch work](docs/ACCEPTANCE.md)
- [Brand assets and usage](docs/BRAND.md)
- [Official brand](docs/BRAND.md)

No default administrator is silently created at startup. Bootstrap creates role templates and a location; the requested initial administrator is created only through the explicit deployment initializer. Dependencies used by the container are pinned in requirements.lock. KOFAD is live on Railway with separate web, PostgreSQL and SMS-worker services; launch blockers and operational limitations remain documented below.

## SMS and initial administrator

Arkesel SMS has a durable outbox, editable customer templates, consent checks, delivery callbacks and retry controls. Debt Settings now define due/overdue timing, reminder cadence and anti-spam limits; Communication Settings define sale/payment/closing/low-stock event behaviour and management notification numbers. Live provider delivery remains disabled until approved Arkesel credentials are configured and validated. See [SMS setup](docs/SMS.md).

The requested ADMIN account uses the private staff gateway. Temporary/bootstrap credentials must be replaced; SMS password recovery uses an individual account recovery phone. See [account recovery](docs/ACCOUNT_RECOVERY.md) and [initial administrator setup](docs/RAILWAY.md#requested-initial-administrator).

[CHALIN03 comparison](docs/CHALIN03_REVIEW.md) records the reviewed features and KOFAD improvements without claiming complete parity.

## Repository map

`core/services.py` owns financial and stock transactions; `core/models.py` owns constraints and schema; `core/views.py` owns scoped screens and endpoints. Templates and static assets make up the interface. Database migrations, integrity tests and remote browser checks are committed alongside the application.

Physical stock-count workflow: [operator guide](docs/STOCK_COUNTS.md).

Partial transfer receipts: [operator guide](docs/STOCK_TRANSFERS.md).

Deployment uses Railway service settings and a CI-gated railway-release branch. See [live deployment and setup](docs/RAILWAY.md).
