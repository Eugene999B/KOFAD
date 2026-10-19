# Upstash Redis REST — opt-in map-search budget

This integration is **disabled by default**. It changes no payment, checkout,
staff login, password, inventory or payroll behavior.

## What it does

KOFAD's customer map search currently uses a local Django cache counter.
When the new feature is enabled, a single Upstash Redis REST request performs
an atomic EVAL of **both** budgets:

- One deployment-wide request count per UTC epoch minute.
- One request count per anonymized session and minute.
- The key expires after 75 seconds.
- Only a keyed SHA-256 hash of the Django session key is transmitted (not the
  session cookie, phone, email, location query or raw customer IP).
- When Upstash is unavailable, **only paid map-search requests are denied**,
  rather than silently dropping the cost budget. Other pages keep working.

The script performs the increment and expiry together; it does **not** rely on
non-atomic Upstash REST pipelines.

## Configure and verify

1. Create a dedicated Upstash Redis database with REST enabled in
   [Upstash Console](https://console.upstash.com/). Check its usage/billing
   limits first. Do not use a temporary expiring database in production.
2. In the Railway *web service* variables set
   `KOFAD_UPSTASH_REST_URL` to the HTTPS REST endpoint issued by Upstash
   (the code only accepts a plain `*.upstash.io` host), and
   `KOFAD_UPSTASH_REST_TOKEN` to the matching REST token.
3. Leave `KOFAD_UPSTASH_REST_ENABLED=0` at first. In
   **Staff → Settings → Connections → Upstash Redis REST**, click
   **Verify provider now**. This only sends authenticated `PING`,
   reports whether Upstash accepted it, and does **not** create Redis keys.
4. After a successful PING and an isolated concurrency/usage test, set
   `KOFAD_UPSTASH_REST_ENABLED=1` on the web service and redeploy.
5. Confirm map-search limit behavior in the test environment. To roll back,
   set the feature flag to `0` and redeploy; the prior local limiter resumes.

The URL and token must **never** be sent to client code, logs, tickets,
screenshots or repository files. All live REST requests have a short timeout,
disabled redirects, a fixed provider hostname pattern, and no error-body
exposure. The staff Connections page shows only prerequisite states.

## Coverage and limitations

This first rollout shares **map lookup** rate limits only. It does not make
Django's entire cache shared and does not alter existing database-backed
customer login throttles, SMS budgets, staff MFA or payment flows.

PING proves Upstash authentication and HTTPS reachability. It does not prove
correctness of concurrent limits; that requires a separate test with controlled
traffic and known quota values.

Run the mocked regression tests with:

```shell
python manage.py test core.test_upstash_redis
```
