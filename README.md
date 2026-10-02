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
