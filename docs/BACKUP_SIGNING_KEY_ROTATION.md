# KOFAD full-backup signature key transition

Full-system archives are AES-256-GCM encrypted with their own passphrase,
**and separately HMAC-signed**. Before this change, the HMAC key was the
Django `DJANGO_SECRET_KEY`, making historic archives unverifiable if the
Django key was rotated without preserving its old value.

## Safe rollout (owner/administrator, not an automated cleanup)

1. Preserve the currently working key securely outside Railway and GitHub.
   Take an encrypted offsite backup and verify its checksum and passphrase.
2. Add **new, independently generated** `KOFAD_BACKUP_SIGNING_KEY` (at least
   32 random characters), plus stable `KOFAD_BACKUP_SIGNING_KEY_ID`
   (for example `backup-2026-10`), via private Railway variables. Never
   reuse the SMS, Brevo, database, Django, or Google Drive HMAC secrets.
3. For older archives already signed by Django, configure
   `KOFAD_BACKUP_PREVIOUS_SIGNING_KEYS_JSON` with a private JSON object
   mapping `"legacy"` to the **former** `DJANGO_SECRET_KEY`. To rotate the
   dedicated key later, add its old ID and value to this map; use a fresh ID
   for the new key. Preserve old values securely for the archive retention
   period and test them in isolation before retiring them.
4. Produce a new encrypted backup and independently validate **both**
   an old archive and a new archive on a disposable database of the exact
   matching schema. Existing restore rules deliberately require exact
   migration history; this change does not alter that constraint.
5. Only after successfully verifying archived files, rotate the Django
   secret and update any other services depending on it. Changing Django's
   signing key can invalidate active sessions and tokens; schedule recovery
   carefully.
6. Never replace/drop the old verification keys until all archives that
   require them have been explicitly retired under documented retention rules.

By default `KOFAD_BACKUP_SIGNING_KEY` is blank, so backup creation stays in
the legacy format and does not silently change production recovery behavior.
Malformed key-ring configuration fails closed; there is no automatic restore,
reset, key generation, or backup deletion.

The archive signature key ID is metadata (not a secret); the key itself and
the legacy key-ring must **never** appear in a repository, client response,
audit log, screenshot or diagnostic artifact.
