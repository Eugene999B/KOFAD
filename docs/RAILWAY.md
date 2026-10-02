# Railway deployment and recovery

KOFAD uses a dedicated Railway project with PostgreSQL, a web service and an SMS worker. Live deployment settings are managed through Railway service configuration. New Railway services no longer accept the deprecated railway.json configuration format.

## Configure staging

1. Create a Railway project with a PostgreSQL service and a GitHub-backed application using this repository.
2. Select the committed Dockerfile. The Railway pre-deploy command applies committed Django migrations. It never generates migrations or resets the schema.
3. Set DATABASE_URL using the PostgreSQL service reference.
4. Set DJANGO_SECRET_KEY to a unique random secret of at least 32 characters. Never commit it.
5. Set DJANGO_ALLOWED_HOSTS to the application's hostname, without a URL scheme.
6. Set CSRF_TRUSTED_ORIGINS to the full HTTPS origin. Set DJANGO_DEBUG=0 and SECURE_SSL_REDIRECT=1.
7. Deploy a verified commit. Run python manage.py bootstrap once in the application service.
8. For the requested initial ADMIN account, follow the protected initial-administrator setup below.
9. Sign in and configure company details, users, groups and location assignments.
10. Add real products and contacts. Opening stock is an approved adjustment; prepare a second authorized user to review it.

The CI suite builds the production image and checks database readiness and login rendering with production settings. The Docker container binds Railway's PORT, runs as an unprivileged user and serves static assets using WhiteNoise. /health/ checks database connectivity. It does not certify migration compatibility or business acceptance.

## Before accepting money

- Complete docs/ACCEPTANCE.md and resolve its launch blockers.
- Validate receipt requirements, tax rules, credit limits, stock valuation, return policy and payment accounts with the business.
- Keep staging data and credentials separate from production.
- Record the release commit, migration list and previous known-good image.
- Confirm staff permissions using real role accounts, including branch isolation and forbidden API requests.
- Confirm receipt printers, mobile layouts, accountant exports and concurrent counter operation.
- Load-test expected data volume. Current reports/lists have documented limits.

## Backups and restore

Configure Railway PostgreSQL backups and an independently secured encrypted off-platform backup. Railway platform backups alone do not satisfy the master plan's full recovery requirement.

Before production migration, run pg_dump --format=custom against the production database from an authorized operator environment. Record the commit, migration versions, SHA-256 checksum, timestamp and table counts in a backup manifest. Keep the manifest and encrypted backup away from the application service.

Restore first into a separate database using pg_restore. Verify counts, sample receipts, customer balances, stock sums, schema migrations and login. Do not point the production app at a restored database until checks pass and the owner authorizes the cutover.

Revoke sessions after recovery by incrementing all Access.session_version values. Preserve the recovery incident and audit records outside the restored snapshot. Rotate compromised secrets if applicable.

The application does not automate backup scheduling, off-platform storage, manifest signing or production restore. Those are launch blockers until an operator configures and rehearses them.

## Account recovery

See [account recovery](ACCOUNT_RECOVERY.md). Authenticator enrollment is no longer required.

## Monitoring and rollback

Monitor health checks, database capacity, application errors, request latency and failed logins. Never log passwords, TOTP keys, session cookies or payment credentials.

If a release fails, revert to the last verified application commit only when its schema remains compatible. Do not blindly reverse financial migrations or overwrite live data. Stop new postings during incident recovery. Verify stock, payment totals and idempotency outcomes before reopening the counter.

## Requested initial administrator

The web service's deployment command is python manage.py initialize_deployment. It serializes migrations with a PostgreSQL advisory lock, applies committed migrations, and performs one-time setup only when KOFAD_INITIAL_ADMIN_PASSWORD is explicitly configured. Existing ADMIN credentials are never reset by a release.

For the first KOFAD deployment, set KOFAD_INITIAL_ADMIN_PASSWORD privately to the requested initial value admin. The deployment initializer invokes:

python manage.py bootstrap_admin --confirm-initial-setup

This creates username ADMIN with full administrative access. The account opens the workspace directly. Users can change passwords under My account. The command refuses to reset or elevate an existing ADMIN account. Remove the temporary deployment variable immediately after setup.

The initial administrator signs in directly with the configured username and password. Password changes are available under My account. Usernames are case-insensitive. GitHub Actions proves this workflow in an isolated database; those CI accounts are destroyed with the test environment.

For Arkesel web/worker configuration, see SMS.md.


## Live KOFAD environment

Project: KOFAD (816fb38a-1d03-4508-ba65-07a8b9de12f1). Environment: production.

- Web: kofad-web, Dockerfile builder, port 8000, healthcheck /health/.
- Worker: kofad-sms, same image source, start command python manage.py process_sms --loop, no public domain or HTTP healthcheck.
- Database: Postgres, PostgreSQL 16, persistent 5 GB volume mounted at /var/lib/postgresql/data, private networking only.
- Both application services run python manage.py initialize_deployment before release.
- Both application services deploy from railway-release. GitHub's promotion workflow advances that branch only after Verify KOFAD succeeds for the current main commit. It does not execute artifacts or code from pull requests.
- The Railway Wait for CI toggle could not be enabled through the connector; the release branch provides the verified-release gate instead.
- The database Backups dashboard was checked on 2026-10-02. It reports backup creation and PITR require Pro; the workspace remains on Hobby at the owner's request. Although a next-backup timestamp appeared after an attempted schedule setting, no backup exists and backup protection is not verified. Do not rely on that timestamp. Independent encrypted off-platform backups and restore rehearsal remain outstanding.

The web service stores DJANGO_SECRET_KEY privately. DATABASE_URL references Postgres.DATABASE_URL. The worker references web-service SMS variables so credentials have one configured source. SMS_ENABLED=0 and SMS_SANDBOX=1 until Arkesel credentials and the sender are verified.

KOFAD_INITIAL_ADMIN_PASSWORD was removed after the first live account was created. No demo users, demo products, or demo transactions are seeded on Railway.

Public login: https://kofad-web-production.up.railway.app/login/ . The initial admin/admin account opens the workspace directly. Change password and the recovery phone are available under My account.
