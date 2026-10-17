# KOFAD weekly encrypted backup to company Google Drive

Status: **prepared, not activated** until the company owner completes Google Apps Script authorization, secret configuration and a successful restore-validation test.

## Owner and destination

- Google account: **kofadimpexenterprise@gmail.com** (never the owner's personal account).
- Company Drive folder: [KOFAD IMPEX ENTERPRISE - Encrypted Weekly Backups](https://drive.google.com/drive/folders/15HUuUOLo6oDqP-RKd2WuKb4flm1EsNo9). This folder belongs to the KOFAD Impex Enterprise Google account.
- Scheduler: a private Google Apps Script owned by the **Kofad Impex Enterprise** account. **No Railway cron, separate Railway app, new database, webhook queue or worker** is used.
- Target schedule: **Sunday, 02:00–03:00 GMT (Ghana time)**. Apps Script clock triggers are approximate, not second-exact.
- Format: existing KOFAD AES-256-GCM encrypted signed full-system backup, \`.kofad.enc\`.
- File names: \`KOFAD-FULL-BACKUP-YYYY-MM-DD-HHmm.kofad.enc\`.
- Retention: the **most recent four verified** backups; previous backups survive failed exports/uploads.
- Verification: SHA-256 of downloaded bytes must equal server header, and Google Drive re-read must produce the same SHA-256 and size. **This does not prove an actual restore**.

## Costs

No new Railway service or background polling. Google Apps Script and Drive may be free **within account quotas**. One export per week uses some existing Railway CPU, memory and outgoing bandwidth, so **zero Railway usage charge cannot be guaranteed**. Google's URL Fetch has a 50 MB response limit and Apps Script execution has a 6-minute limit. We conservatively reject server exports larger than 40 MiB; there is no truncation or partial success. The existing in-site encrypted backup can support at most 100 MiB; applications exceeding Apps Script's limit need a different transport (not a blind increase in limit).

## One-time owner setup (required for activation)

1. From **Railway → KOFAD → kofad-web → Variables** only (not the SMS worker), configure:
   - \`KOFAD_GDRIVE_BACKUP_ENABLED=1\`
   - \`KOFAD_GDRIVE_BACKUP_HMAC_KEY=<random secret of >=32 characters>\`
   - \`KOFAD_GDRIVE_BACKUP_PASSPHRASE=<strong unique secret of >=24 characters>\`
   Use independently generated secrets. These values are never hardcoded in repository code or URLs.
2. **Store the encryption passphrase offline** in the company recovery register, away from the Google Drive account and away from Railway; retain the original **DJANGO_SECRET_KEY** in a separate privileged recovery/escrow process too. Existing restore uses an HMAC derived from that Django key, so losing/rotating it may invalidate historical signatures. Never paste these into emails, Google Drive files or Apps Script source. A Google script does **not** require either the backup passphrase or the Django key.
3. While signed into \`kofadimpexenterprise@gmail.com\`, create a **new private project** at https://script.google.com/ . Copy all source from \`ops/google_drive_weekly_backup.gs\` into the project's \`Code.gs\`. Do not share it publicly or with non-administrators.
4. Under **Project settings → Script properties**, set:
   - \`KOFAD_BACKUP_SIGNING_KEY\` = the exact value of Railway \`KOFAD_GDRIVE_BACKUP_HMAC_KEY\`
   - \`KOFAD_BACKUP_FOLDER_ID=15HUuUOLo6oDqP-RKd2WuKb4flm1EsNo9\`
5. Save and run \`installWeeklyKofadTrigger\` **once**. The company Google account must **approve Google's consent screens** for Drive, Mail and external HTTPS requests. The function replaces only the weekly KOFAD trigger, avoiding duplicates.
6. Run \`weeklyKofadBackup\` **once manually** and verify a dated encrypted file appears in the folder, with a \`verified-sha256=\` description and a success email to \`kofadimpexenterprise@gmail.com\`. Verify the file's bytes match the digest. Then, in KOFAD's existing **Settings → Backup → Validate**, test that file using the offline encryption passphrase. Record a separate restoration rehearsal in an isolated database.
7. Check Google Apps Script **Executions** after the first scheduled Sunday and ensure the **latest four backups** are present. A failed attempt sends an alert and **never deletes previous successful backups**.

**Do not enable a schedule and leave it untested.** Until steps 1–7 succeed, automated backups are **not running**.

## Data security and controls

- Server endpoint: \`POST https://staff.kofadimpex.com/settings/backup/google/export/\`.
- This is *export-only*. It cannot restore or reset data and never uses the administrator password.
- A versioned HMAC-SHA256 signature covers method, fixed path and short-lived Unix timestamp. Request signature is provided in HTTP headers, **never in a query string**. The server enforces a 5-minute signature window and a separate long secret, with the feature **disabled by default**.
- Accepts requests only on the official staff hostname. The script does not accept an arbitrary export URL, reducing accidental delivery to third parties.
- Backup content is already encrypted server-side **before Google receives it**. Google script only sees ciphertext.
- The Google app does not store the encryption passphrase. This means a stolen Drive account alone is not enough to decrypt content, but the offline passphrase and the original KOFAD signing key must remain recoverable.
- The script uses a single-instance lock and skips duplicate confirmed backups on the same Ghana date. Old files are pruned **only after** successfully downloading, uploading, re-reading and hashing a fresh archive.
- Existing manual Backup, Validate, Restore and Reset workflows remain unchanged.
- The script can validate upload integrity, not plaintext DB/business consistency. A restore rehearsal is required, and KOFAD currently requires matching migration schema for a restore. **Releases must retain a compatible recovery path for backups from older weeks.**

## Troubleshooting

- HTTP 404: feature disabled, missing server secrets or incorrect host.
- HTTP 403: HMAC mismatch or expired timestamp; check Script Properties and UTC clock.
- HTTP 409: an export is already underway.
- HTTP 413: KOFAD's encrypted backup exceeds export/script size limits or the existing archive cap. **Do not increase thresholds blindly**; plan a streaming/bulk snapshot transport with chunking or separate storage.
- HTTP 503: database snapshot could not be acquired or exporter failed. Existing backups must be kept.
- Google Drive quota exceeded: do **not** delete the last known-good backups merely to clear space; review quota and safe relocation first.
- No success email: check Drive folder, Google Apps Script Executions and Script Property \`KOFAD_LAST_BACKUP_SUCCESS_UTC\`; mail delivery can fail separately after a valid upload.
- If a Sunday schedule fails, fix the problem and rerun manually; do not assume it will retry automatically.

## Hubtel Direct full-system coverage (backup v3)

New full-system archives include `core`, `marketplace`, and `hubtel_direct` models, including direct-provider transaction records, customer debit authorizations/mandates, debt collection requests, payroll transfer state and refund cases. The inner signed backup format is **v3**; the outer AES-256-GCM encrypted file wrapper remains unchanged.

Earlier signed backup **v2** archives were missing `hubtel_direct`. They remain **readable** subject to the original exact-migration checks, but they **must not** overwrite a database containing live Hubtel Direct records. KOFAD rejects that destructive legacy restore and directs recovery to an isolated clean database. An old archive alone cannot recover provider authorization/collection state.

After deploying v3, generate and verify a **new** encrypted archive before considering disaster recovery complete. The October 11, 2026 archive was created before this v3 code and must be treated as **unverified for Hubtel Direct coverage** unless its authenticated inner manifest proves otherwise. Never silently edit or delete existing financial state to make an older restore succeed. A successful validation still does not replace a real isolated restore rehearsal.

**Automatic debit recovery hold:** restoring a v3 snapshot marks restored pending/approved/cancellation-pending customer mandates as `suspended` and moves planned/in-flight debit attempts to `finance_review`. These statuses deliberately block new wallet debits based only on stale backup data. An authorized operator must reconcile current provider mandates, customer consent, outstanding invoices and all unresolved attempts **before** considering reactivation. Provider-confirmed receipts and ledger allocations are preserved; no payout or debit is sent by the restore. The restore audit records counts of held mandates/attempts.

## Verification evidence required

- Passing Django unit tests \`core.test_google_drive_backup\`, full KOFAD CI and no unintended Android/Hubtel changes.
- One **successful real backup**, matching SHA-256 and file size, and explicit KOFAD website Validate confirmation.
- Successful **isolated restore test** before describing the backup strategy as disaster-recovery-complete.
- Last four successful weekly backup dates and independently stored owner recovery credentials.
