# KOFAD private staff AI — controlled free-model tool planning

Status: code implemented behind `KOFAD_STAFF_AI_PLANNER_ENABLED` feature flag.
Production rollout requires CI success and Railway activation.

## What the model does — and does not do

KOFAD first authenticates the staff user and checks current staff security steps.
The backend strips the original message to a strict generic business-vocabulary
allowlist. Personal names, contact details, amounts, dates, branch names, order
references, provider credentials, product names, and the full message are never
forwarded to an external LLM.

The existing free-eligible Gemini route is considered only when
`KOFAD_GEMINI_ENABLED=1`, `KOFAD_GEMINI_FREE_TIER_CONFIRMED=1`, and
`GEMINI_API_KEY` is already configured. A failed Gemini plan may try the
existing `openrouter/free` path ONLY with `max_price.prompt=0`,
`max_price.completion=0`, and `data_collection=deny` and ONLY when
`KOFAD_OPENROUTER_FREE_ENABLED=1` and `OPENROUTER_API_KEY` exist. The
system never requests a paid model or transmits actual business query results
to either provider. If either provider is not confirmed free, disable it.
Cloud providers have independent quotas, pricing and privacy policies;
never interpret a free-eligible model name as a contractual price guarantee.

The model returns JSON tool names only. Unknown names are rejected, at most
four known tools are executed, and KOFAD runs those fixed tools locally via
the ORM with current branch and role checks. Results never go back to a model.
No generated SQL, shell execution, payment, approval, message sending, or
customer write tool is exposed to the model. All private summarization is
performed locally.

Mutation-like or security-related requests bypass external planning entirely.
The deterministic staff assistant remains the fallback whenever provider calls
are unavailable, capped or malformed. There is an additional best-effort
20-call-per-day planning budget across the website to protect free quotas,
on top of the staff assistant's existing per-user daily request limit.

## Known limitations and next safe steps

This is a bounded *model-guided planner*, not autonomous AGI and not a
general-purpose private-business LLM. It improves interpretation of multi-topic
questions and chooses local evidence tools but cannot reason freely over private
figures without a separately approved privacy-safe model/processing policy.
The entire private chat history is browser-only and is not transmitted.

A future upgrade can add structured evidence citations, local deterministic
cross-report discrepancy checks, richer date and comparison parsing, durable
approval requests, consented staff actions with audit trails, and an evaluation
suite of real-world phrasing (with all PII removed).

## Production verification

1. Run the focused staff AI and KOFAD CI test suites.
2. Set `KOFAD_STAFF_AI_PLANNER_ENABLED=1` on `kofad-web` ONLY. Do not copy
   provider keys or the flag to a browser, Android, or the SMS worker.
3. Confirm an existing free-provider flag and key are configured. If none is
   configured, responses remain fully local: this is expected and safe.
4. Test multi-topic questions as manager and restricted staff. Ensure
   unauthorized financial sections are never shown.
5. Check `external_ai_used` in authenticated JSON responses to verify when
   a free provider actually selected tools. This indicates *planning only*,
   not sending business evidence to a provider.
6. Disable the flag instantly if free quota, privacy, rate or latency issues
   arise; deterministic business tools continue working.

## Related security settings

Cloudflare root zone `kofadimpex.com` was independently checked on
2026-10-23. Railway origin certificates for the root/wildcard were verified
valid prior to changing Cloudflare TLS encryption mode to Full (strict),
minimum TLS 1.2, and Always Use HTTPS on. All three settings were read back
successfully. Existing DNS routing, provider webhooks, and WAF rules were
not modified. Cloudflare settings affect proxied hostnames; DNS-only
hostnames still depend on origin HTTPS policy.

