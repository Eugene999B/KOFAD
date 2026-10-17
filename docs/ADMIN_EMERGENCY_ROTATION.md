# KOFAD ADMIN emergency password rotation (deployment safety)

Emergency rotation is **not** normal password management. Use authenticated account settings wherever possible. This mechanism should be used only by an authorized owner after an account-recovery incident and with a strong unique temporary password.

## New one-time requirement

A deployment will **not** reset ADMIN's password just because an old `KOFAD_ADMIN_ROTATION_PASSWORD` remains set.

For a deliberate emergency recovery, configure **both** variables on the Railway **web** service through authorized secure settings:

- `KOFAD_ADMIN_ROTATION_PASSWORD`: a freshly generated, high-entropy temporary password.
- `KOFAD_ADMIN_ROTATION_REQUEST_ID`: a **new** UUID for this specific recovery attempt (for example, create one locally with Python `uuid.uuid4()`). This is a request identifier, not a secret.

Do not paste the password into GitHub, logs, issues, documents, or chats. Verify the ADMIN account is active and authorized. The deployment command checks password policy. It never enables or promotes an inactive/non-superuser ADMIN account.

On the **first** successful use of that identifier, the command records a hash-linked security audit event and either:
- Changes the password to the temporary credential, flags the next sign-in for a password change, resets MFA enrollment for recovery, and increments the session version to invalidate old sessions; or
- If ADMIN already uses the temporary password, **consumes the identifier without touching MFA or sessions**.

A later deployment with the **same** request identifier will **not** change the password again, even if the owner has subsequently changed it. Every separately authorized recovery requires a different UUID, and all attempts should be investigated through audit events.

## Close the recovery

1. Confirm the authorized owner can sign in and replace the temporary password, and re-enroll MFA if it was reset.
2. **Remove both rotation environment variables** from Railway once the operation is confirmed; treat the temporary password as compromised after the recovery window.
3. Ensure no stale variables are present on another deploy runner. KOFAD's web and SMS services share the same database and serialize deployment migrations.
4. Review `admin.emergency_credential_rotated` / `admin.emergency_credential_rotation_consumed` audit events and inspect new sessions.

This mechanism does not bypass the required user identity/recovery procedures, and it should never be used to silently change somebody else's credentials.
