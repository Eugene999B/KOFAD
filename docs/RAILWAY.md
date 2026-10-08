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

See [account recovery](ACCOUNT_RECOVERY.md). Privileged staff accounts require authenticator MFA when `PRIVILEGED_MFA_ENFORCED=1`; verified password recovery resets MFA so the owner can enrol a new authenticator.

## Monitoring and rollback

Monitor health checks, database capacity, application errors, request latency and failed logins. Never log passwords, TOTP keys, session cookies or payment credentials.

If a release fails, revert to the last verified application commit only when its schema remains compatible. Do not blindly reverse financial migrations or overwrite live data. Stop new postings during incident recovery. Verify stock, payment totals and idempotency outcomes before reopening the counter.

## Requested initial administrator

The web service's deployment command is `python manage.py initialize_deployment`. It serializes migrations with a PostgreSQL advisory lock, applies committed migrations, and performs one-time setup only when a private bootstrap credential is explicitly configured.

For a new installation, generate a unique random temporary administrator credential outside the repository and provide it only through the deployment environment. The bootstrap creates `ADMIN` with full administrative access and **forces an immediate password change**. Remove the bootstrap variable after creation. Never commit, document or reuse an administrator password.

If an old bootstrap credential has ever appeared in source control or documentation, treat it as permanently compromised even after the text is removed. KOFAD supports a one-time deployment variable, `KOFAD_ADMIN_ROTATION_PASSWORD`, for an emergency ADMIN rotation; the deployment clears existing ADMIN MFA enrollment, revokes the credential through Django's normal password-change security path, and forces the temporary credential to be replaced at first sign-in. Remove that rotation variable immediately after the successful deployment.

GitHub Actions proves bootstrap behavior only against isolated CI databases; CI credentials are not production credentials.

For Arkesel web/worker configuration, see SMS.md.

## Live KOFAD environment

Project: KOFAD (816fb38a-1d03-4508-ba65-07a8b9de12f1). Environment: production.

- Web: kofad-web, Dockerfile builder, port 8000, healthcheck /health/.
- Worker: kofad-sms, same image source, start command python manage.py process_sms --loop, no public domain or HTTP healthcheck.
- Database: Postgres, PostgreSQL 16, persistent 5 GB volume mounted at /var/lib/postgresql/data, private networking only.
- Both application services run python manage.py initialize_deployment before release.
- Application services must be pinned to a specific verified release commit in Railway production. The `railway-release` branch remains a human-readable promotion pointer, but a direct branch push must not automatically change the running production commit.
- GitHub's promotion workflow advances `railway-release` only after Verify KOFAD succeeds for the current main commit. GitHub Actions are pinned to immutable commit SHAs.
- The database Backups dashboard was checked on 2026-10-02. It reports backup creation and PITR require Pro; the workspace remains on Hobby at the owner's request. Do not rely on Railway backup protection on Hobby. KOFAD's application backup download is encrypted with AES-256-GCM using a passphrase supplied by the administrator; store the encrypted file and its passphrase separately and rehearse restore. An automated off-platform schedule is still recommended.

The web service stores DJANGO_SECRET_KEY privately. DATABASE_URL references Postgres.DATABASE_URL. The worker references web-service SMS variables so credentials have one configured source. SMS_ENABLED=0 and SMS_SANDBOX=1 until Arkesel credentials and the sender are verified.

KOFAD_INITIAL_ADMIN_PASSWORD was removed after the first live account was created. No demo users, demo products, or demo transactions are seeded on Railway.

Staff access uses the official `staff.kofadimpex.com` origin. Do not publish or rely on Railway-generated service URLs as a second user-facing login path.


## Official domains

- https://kofadimpex.com — company homepage and public staff-card verification.
- https://market.kofadimpex.com/market/ — customer shopping and account.
- https://staff.kofadimpex.com/workspace/ — authenticated staff operations.
- www redirects to the company homepage.

Railway manages root and wildcard DNS/TLS on the existing web service. Unknown hosts remain rejected by the explicit allowlist. No extra services are needed.

Canonical routing keeps the customer and staff pages on separate origins. Wrong-host writes are rejected instead of forwarding submitted credentials or transactions. Production uses a Secure, HttpOnly, host-only __Host-kofad_session cookie. Do not configure a parent-domain cookie or sibling CSRF trusted origins. Existing users must sign in once again after the cookie-name change.

This separates browser origins, not application infrastructure: the app and database remain shared, and role/branch checks still govern every staff operation. Saved signed SMS/WhatsApp/Paystack webhook URLs remain valid; provider authentication still applies.

Include healthcheck.railway.app in DJANGO_ALLOWED_HOSTS. Leave CSRF_TRUSTED_ORIGINS empty for this same-origin form deployment. Provider callbacks and customer/staff links must use the official KOFAD domains. SMS_PUBLIC_ORIGIN and WHATSAPP_PUBLIC_ORIGIN remain https://kofadimpex.com.
