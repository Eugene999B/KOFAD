# KOFAD IMPEX ENTERPRISE

A unified retail and wholesale operating system for KOFAD. One catalog, pack-aware stock, a fast counter, and traceable financial records.

## Technology decision
Python 3.12, Django 5.2 LTS, PostgreSQL 16, server-rendered HTML and focused vanilla JavaScript. A modular monolith keeps transactional boundaries explicit and avoids running separate frontend and API infrastructure. PostgreSQL row locks and database constraints protect concurrent stock and payment operations. Railway is the intended deployment target.

## Product direction
A calm trading-house identity: forest green, warm paper, brass, compact ledgers, generous typography and a custom vector K mark. All dashboard figures must come from persisted records; there are no fabricated business metrics.

## Source of requirements
KOFAD IMPEX ENTERPRISE Master Software Plan, version 1.0, 2 October 2026, supplied by the owner. Treat its sample prices and metrics as illustrations, not production records. Business policy and provider credentials must be configured before launch.

## Delivery discipline
Implementation and commits are made directly in this GitHub repository. GitHub Actions is the remote verification environment. No local checkout is required.

The implementation, operational guide, acceptance matrix and launch limitations will be added alongside the application. A successful build is not a substitute for a production security review, restore rehearsal and owner acceptance of tax, credit and approval policies.

## License
Proprietary. Copyright KOFAD IMPEX ENTERPRISE. No license to use, distribute or sublicense is granted by public repository visibility.

## Application

The repository now contains a Django/PostgreSQL implementation of the core operating workspace:

- Sales counter with mixed retail/wholesale and unit/pack carts, held sales, split payments and credit.
- Products, stock movements, independent adjustment approvals and dispatch/receive transfers.
- Customer/supplier records, purchase receiving, debt allocation, returns and expenses.
- Daily channel reconciliation with independent verification and a posting lock after closing.
- Receipts, statements, transaction reports and PDF/Excel/Word/CSV exports.
- Custom roles, location access, privileged TOTP, session revocation and audit evidence.
- Original SVG identity and responsive desktop/mobile screens.

**Status:** pre-production implementation. The entire master plan is not complete. Tax handling, broader corrections/approvals, provider messaging, report families, advanced catalog/counts and operational recovery work remain. Read the [acceptance matrix](docs/ACCEPTANCE.md) before using real money or business data.

## Verification

[![Verify KOFAD](https://github.com/Eugene999B/KOFAD/actions/workflows/ci.yml/badge.svg)](https://github.com/Eugene999B/KOFAD/actions/workflows/ci.yml)

GitHub Actions runs PostgreSQL-backed business and concurrency tests, migration checks, dependency auditing, and Chromium desktop/mobile navigation and checkout checks. Browser screenshots are available in the run's browser-evidence artifact. Demo records are explicitly labeled and only permitted in DEBUG environments.

## Deployment and operations

- [Railway deployment and recovery](docs/RAILWAY.md)
- [Architecture and integrity rules](docs/ARCHITECTURE.md)
- [Acceptance and remaining launch work](docs/ACCEPTANCE.md)
- [Brand assets and usage](docs/BRAND.md)
- [Logo](static/brand/kofad-logo.svg)

No default production password exists. Bootstrap creates role templates and a location; an operator creates the owner account and completes configuration. Dependencies used by the container are pinned in requirements.lock. Railway is configured but has not been deployed.

## Repository map

`core/services.py` owns financial and stock transactions; `core/models.py` owns constraints and schema; `core/views.py` owns scoped screens and endpoints. Templates and static assets make up the interface. Database migrations, integrity tests and remote browser checks are committed alongside the application.
