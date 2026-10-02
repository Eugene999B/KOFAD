# Railway deployment and recovery

This repository is prepared for Railway. It has not been deployed automatically. Use a staging environment first.

## Configure staging

1. Create a Railway project with a PostgreSQL service and a GitHub-backed application using this repository.
2. Select the committed Dockerfile. The Railway pre-deploy command applies committed Django migrations. It never generates migrations or resets the schema.
3. Set DATABASE_URL using the PostgreSQL service reference.
4. Set DJANGO_SECRET_KEY to a unique random secret of at least 32 characters. Never commit it.
5. Set DJANGO_ALLOWED_HOSTS to the application's hostname, without a URL scheme.
6. Set CSRF_TRUSTED_ORIGINS to the full HTTPS origin. Set DJANGO_DEBUG=0 and SECURE_SSL_REDIRECT=1.
7. Deploy a verified commit. Run python manage.py bootstrap once in the application service.
8. Run python manage.py createsuperuser interactively in Railway's service shell. There is no default owner password.
9. Sign in, enroll the owner's authenticator and configure company details, users, groups and location assignments.
10. Add real products and contacts. Opening stock is an approved adjustment; prepare a second authorized user to review it.

The Docker container binds Railway's PORT, runs as an unprivileged user and serves static assets using WhiteNoise. /health/ checks database connectivity. It does not certify migration compatibility or business acceptance.

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

## MFA recovery

From the authorized service shell:
python manage.py reset_mfa USERNAME --reason "Verified owner identity under recovery procedure"

This clears enrollment, revokes sessions and records an audit event. Keep at least two secured operator access paths. Self-service recovery codes are not implemented.

## Monitoring and rollback

Monitor health checks, database capacity, application errors, request latency and failed logins. Never log passwords, TOTP keys, session cookies or payment credentials.

If a release fails, revert to the last verified application commit only when its schema remains compatible. Do not blindly reverse financial migrations or overwrite live data. Stop new postings during incident recovery. Verify stock, payment totals and idempotency outcomes before reopening the counter.
