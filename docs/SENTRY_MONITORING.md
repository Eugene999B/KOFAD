# Sentry for KOFAD — controlled, privacy-minimized backend error monitoring

Sentry is an optional error-monitoring service for the Django marketplace,
staff portal and worker. KOFAD uses `sentry-sdk==2.72.0`, loaded only when
**both** `KOFAD_SENTRY_ENABLED=1` and `KOFAD_SENTRY_DSN` are configured.

## Data minimization

The integration sends **only** an exception class/type, code filename,
function, module and line number, plus release/environment metadata.

KOFAD deliberately discards exception messages and values, request URLs,
headers, query strings, POST bodies, cookies, user identity, IP addresses,
breadcrumbs, log messages, stack locals and all extra contexts/tags.
Non-exception log messages are dropped.

Session replay, browser telemetry, AI prompt capture, transaction tracing,
profiling, and performance sampling are **not** enabled. A Sentry event is
still third-party processing of error metadata; review its project privacy
settings, region and retention before connecting production.

The SDK's `before_send` callback is an allowlist, and the code has privacy
regression tests that include fake customer, password and payment values.

## Provisioning and activation

1. Create a free Sentry organisation and **Python / Django** project using
   an approved company account (https://sentry.io). Choose the organisation's
   appropriate data region and retention controls.
2. Copy the project's **HTTPS DSN** from Sentry's Client Keys settings.
   Store it in Railway secret environment variables, not in GitHub or an
   application settings screen.
3. On the `kofad-web` service set:
   - `KOFAD_SENTRY_DSN=https://...project-dsn...`
   - `KOFAD_SENTRY_ENABLED=1`
   - `KOFAD_SENTRY_SAMPLE_RATE=0.25` (optional; 25% of eligible errors)
4. Optionally configure the same project/DSN on `kofad-sms` if worker
   exception alerts are wanted. Do not accidentally export SMS payloads;
   the same event allowlist is enforced in both processes.
5. Deploy, verify the service stays healthy, and create a **controlled,
   non-production exception** with no real customer data to confirm one
   sanitized event appears in the Sentry Issues page.
6. In staff Settings → Connections, inspect "Sentry error monitoring".
   Configuration presence is not proof of event delivery.
7. Configure Sentry project email alerts and quota limits in its console.
   For immediate rollback, set `KOFAD_SENTRY_ENABLED=0` per service.

No database migration is required. If the DSN is absent, the SDK is installed
but never initialized. No data is sent without explicit configuration.

## Deliberate limitations

- Stack location and exception type help diagnose recurrent failures; message
  details and input values must be investigated locally in access-controlled logs.
- This is **error monitoring**, not a replacement for security audits,
  reconciliation checks, uptime probes or distributed traces.
- All HTTP and performance traces are disabled until a separate privacy review.
- Do not test by throwing an error in a live payment or order flow.

Official references:
- https://docs.sentry.io/platforms/python/integrations/django/
- https://docs.sentry.io/platforms/python/data-management/sensitive-data/
