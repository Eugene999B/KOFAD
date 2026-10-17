# KOFAD full backup, recovery, reset, and role-permission coverage

## What a complete backup includes

The signed and encrypted version-3 archive includes the managed business
models for **core**, **marketplace**, and **hubtel_direct**: sales, stock,
creditors, debts, allocations, payroll, customer accounts, orders, messages,
audit records, provider transaction state, payment mandates, refund cases and
related accounting data. The same archive includes Django's durable
`auth.user`, `auth.group`, `auth.permission`,
`contenttypes.contenttype` and `admin.logentry` records, including **custom
roles, individual staff permissions, role membership and assigned branches**.

Ephemeral login challenges and rate-limits are deliberately not restored;
existing login sessions are invalidated by destructive recovery. The backup
does not include a separate export of external providers' databases, external
Drive documents, or third-party transactions that KOFAD never stored.

## Restore (recover an earlier snapshot)

- Requires an authorized system administrator, confirmation, password, and a
  recently downloaded safety backup. Never restore casually on live records.
- Verifies the archive signature, checksum, exact migration schema and
  encrypted passphrase before accepting it. A historic archive may require
  an isolated copy of the matching application release.
- Recovers the saved role groups, permissions, staff membership and their
  business-location assignments as they were when the archive was made.
  A post-backup role or permission change will be rolled back.
- For Hubtel, restored debit mandates/unsent attempts are **held for manual
  provider reconciliation**, not automatically dispatched or retried.
- Older v2 archives lack Hubtel Direct state; they must not erase active
  Hubtel records and are rejected on databases containing such records.

## Reset (fresh business start)

- Requires the same guarded administrator and recent-safety-backup process.
  This is irreversible against the current database unless the backup can
  actually be restored. **Never use reset to repair a reporting discrepancy.**
- Clears all managed **core**, **marketplace**, **hubtel_direct** business
  tables, their relationship/join tables, current sessions and Django admin
  action logs, and removes non-administrator staff and their custom roles.
- Recreates only the authorized administrator, a blank company, a blank main
  branch, and the required administrator access record. Preserves Django
  migration history, content types and permission definitions: deleting these
  would make KOFAD unusable, not more completely reset.
- Restores six clean, reusable role templates: **Owner, Manager, Cashier,
  Storekeeper, Accountant and Auditor**. All 11 operational permissions
  checked by KOFAD's server are available in Administration → Roles, as well
  as for direct staff grants. Owners/Managers have the full operational grant
  set; narrower defaults follow least privilege.
- Rechecks that every managed business model is empty other than the three
  minimum shell records, and that every required role grant exists. Errors
  abort the transaction rather than returning an incomplete "success".

## Role grants vs system-administrator privileges

The role editor intentionally lists only **operational permissions that the
KOFAD application enforces**. Arbitrary Django-generated model permissions
are not automatically safe replacements for business approval checks and are
not silently exposed for assignment. Superuser account recovery, staff role
editing and confidential backup/restore controls remain separately protected.

Custom roles and individual grants survive **backup and restore**, not
**fresh reset**. For the latter, deliberately rebuild custom roles after
confirming fresh access requirements. Role names alone do not give access:
the account must be active, have an assigned branch and pass the server-side
permissions and approval controls.

## Validation still required for disaster-recovery certification

Verify a new v3 encrypted offsite archive, confirm server and Drive SHA-256
checksums, successfully decrypt and validate the signed contents using the
company's privately retained recovery secrets, and rehearse a complete
restore on an isolated, matching-schema database. **Do not paste secrets
into chat, GitHub or Drive.** No code test can certify untested production
backup credentials, a Google Apps Script weekly trigger, or provider-side
settlement authority.
