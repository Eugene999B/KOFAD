# CHALIN03 comparison and KOFAD direction

Reviewed the CHALIN03 repository's README, complete source path inventory, Spare Parts help topics, SMS routes, SMS submission adapter and reliability service. This is a source/design comparison; it is not a complete independent audit of CHALIN03's running production system. No CHALIN03 code, settings, credentials or live records were modified.

CHALIN03 covers Spare Parts, Mining, Equipment Sales & Hire, Group Executive control, workforce/payroll, documents, signatures, accounting intelligence and recovery. Its broad documented feature coverage provides a useful reference. Mining, hire and payroll are separate businesses and are not silently added to KOFAD's retail/wholesale scope.

| CHALIN03 capability | KOFAD treatment |
| --- | --- |
| Scoped stores/sites and permission checks | Branch assignments and service-level permissions already enforced |
| Financial corrections with original evidence | Independent expense/payment reversal and sale void workflows already implemented |
| Transfer approval, dispatch and receive | Separate state transitions and movement timing already implemented |
| Independent closing verification | Already implemented with immutable closing evidence |
| Arkesel, optional future provider | Arkesel adapter plus extensible registry; no ambiguous automatic failover |
| Accepted/delivered/unknown distinctions | Explicit statuses and attempt-scoped, token-authenticated callbacks |
| Message credit estimates | GSM extension accounting and UTF-16 code-unit handling, including emoji |
| SMS retry protection | Durable outbox, per-attempt records, bounded throttling retries, unknown-result hold |
| Receipt, debt and payment messages | Editable validated templates and review-before-queue workflow |
| Signed recovery manifests and restore drills | Still a required operational workstream; do not claim equivalence yet |
| Detailed workforce and document lifecycle | Future scope; requires a KOFAD-specific business case |
| Full accounting intelligence and consolidated executive reports | Basic profit/valuation/aging exists; broader analytics remain planned |

## Improvements implemented in this iteration

- SMS submission runs outside the cashier's request in a durable worker.
- Per-attempt callback tokens reduce the impact of a shared callback credential.
- Unknown provider outcomes cannot silently switch gateways and duplicate billing.
- Sandbox evidence cannot be mistaken for real delivery.
- UTF-16 estimates count non-BMP characters correctly.
- The owner requested direct administrator sign-in. Password change is optional in My account; SMS recovery is available after live delivery and per-account numbers are configured.

These are concrete design improvements, not a claim that the whole KOFAD platform already exceeds CHALIN03 in feature breadth. The remaining acceptance work stays visible in ACCEPTANCE.md.
