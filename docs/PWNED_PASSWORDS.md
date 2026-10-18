# Have I Been Pwned Pwned Passwords — KOFAD

This keyless, free API checks whether a **newly chosen** customer or staff
password is known to have appeared in breach data. It is **not** the paid HIBP
email/domain breach-search API.

## Coverage

KOFAD registers `core.password_validation.PwnedPasswordValidator` alongside
Django's existing minimum-length, common-password and numeric-password
validators. Django password validation is already used in:

- Customer registration, password change, and password recovery.
- Staff password change and recovery.
- Staff invitation acceptance and administrator password-setting actions.

Existing passwords are not sent to HIBP at login or silently changed. Accounts
using Google OAuth are unaffected unless they explicitly set a local password.

## Privacy

- A completed password is SHA-1 hashed locally **only for lookup**. Only the
  first **five hex characters** are sent to HIBP over HTTPS.
- KOFAD compares the returned 35-character suffixes locally and rejects
  matches with a positive breach count. Padded responses use zero counts.
- No full password, full SHA-1 digest, suffix, username, or account identifier
  is sent or logged. Django keeps using its existing password storage hasher.
- This check runs on form submission, **never on every keystroke or login**.

## Release and operations

1. Merge the reviewed change through the protected `railway-release` PR
   process only after **Verify KOFAD** succeeds.
2. Set `KOFAD_PWNED_PASSWORDS_ENABLED=1` for the Railway **web application**
   service. No API key or payment information is required. This flag defaults
   to `0` for controlled activation and quick rollback.
3. Confirm a normal fresh password succeeds and a known-compromised test
   password is rejected on **both** customer and staff password forms.
4. Inspect the staff Settings → Connections → Pwned Passwords row. That row
   reflects configuration; it does not itself call or prove HIBP health.
5. Monitor generic "Pwned Passwords check unavailable" warnings; investigate
   HTTPS egress, upstream errors, and latency. Warnings contain no secrets.
6. Roll back to `KOFAD_PWNED_PASSWORDS_ENABLED=0` if lookup latency or API
   availability causes issues. Existing Django validators remain active.

Timeouts are 1 second to connect and 2 seconds to read. On network/provider
failure KOFAD **fails open for this additional check**, preserving the existing
local Django password policy. This is an explicit availability compromise;
do not claim every password was breach-screened during an outage.

Official specification: https://haveibeenpwned.com/API/v3#PwnedPasswords
