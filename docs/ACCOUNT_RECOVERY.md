# Account access and SMS recovery

Normal login accepts the username (case-insensitive) and password, then opens the workspace. No password change or authenticator step is mandatory. Migration 0009 performs the explicitly requested one-time ADMIN password restoration, clears obsolete authenticator state and revokes the affected administrator's old sessions. Future releases do not reset passwords.

## Inside the workspace

Open **My account** to change your password or save your recovery phone. Both changes require the current password. Administrators can manage each staff member's recovery number in **Administration → Accesss** (the Django access-record list), also linked from Company settings and My account. Numbers are normalized to international format. The company contact phone is separate.

## Forgotten password

The sign-in page links to **Forgot password**. Enter the account username. Recovery sends to that account's saved number only; a submitted phone number cannot redirect delivery.

The code and a new password are entered on the recovery page. Codes expire after ten minutes, allow five attempts, are bound to the requesting browser session and work once. Resending invalidates the previous challenge. Password/phone changes and inactive accounts reject old codes. Successful reset revokes existing sessions and clears the username's password lockout.

Only keyed code digests are stored. Plain codes are not written to audit records, customer communications or application logs. Requests have generic responses, a three-per-account hourly budget and a 100-request aggregate hourly cap. A failed or uncertain provider submission never permits reset.

## Arkesel activation

Set ARKESEL_API_KEY privately in Railway, configure the approved SMS_SENDER_ID, set SMS_ENABLED=1 and SMS_SANDBOX=0. Save the account recovery number. Without working live configuration the UI clearly says recovery is unavailable. No test code is exposed in production.

The existing SMS provider adapter registry is reused, so future installed adapters can serve recovery too. Recovery submits one transactional SMS directly, without a customer-contact record or automatic resend. Provider acceptance is not proof of handset delivery.

Tests mock delivery. A real handset-delivery test requires the owner's Arkesel configuration and intended account number.
