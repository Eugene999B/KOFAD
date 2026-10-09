# Customer Google registration, staff invitations and responsive marketplace

This feature is proposed in PR #100. Merge only after GitHub CI/browser checks pass and test the Railway release health checks. It is distinct from Gmail API sender activation in `docs/email-activation.md`.

## Customer registration and linking

- Guests may browse KOFAD Market before authentication; the catalog has a visible **Sign in or create account** entry point. The welcome page offers Google or phone registration.
- Google's existing OIDC flow validates state, nonce, PKCE, ID-token signature and claims. **Google's immutable `sub`** identifies the Google account. A verified Google email must **never** automatically merge with an unrelated existing customer account.
- New Google identities can submit their name to create a customer with **no phone** and **no local password**. The Google subject is bound to that customer; verified email is recorded.
- They may later opt to add and verify a phone with SMS. The existing phone owner cannot be displaced. A recently reauthenticated Google user can add a local password.
- Phone-registered customers retain password sign-in and optional verified email/Google linking.
- A phone number **is required at checkout when a contact or MoMo number is needed**, not for registration. The checkout form accepts a number provided for an individual order.
- Passwordless accounts must sign in with Google; they must create a local password before attempting verified email/password sign-in or disconnecting their only Google identity.
- Customer notifications remain opt-in; Google sign-in does not subscribe users automatically.
- Never bypass the explicit password+OTP checks for existing phone accounts.

## Staff onboarding

Only a system administrator can create staff accounts, roles and branch access. A new staff user is created with an **unusable password and inactive status**. No administrator-chosen password is delivered or displayed.

The administrator selects:
1. A username, name, assigned role, permitted branches and applicable additional permissions.
2. An invitation channel: SMS (valid mobile number), company email (email address) or WhatsApp (valid mobile number, opt-in and preapproved Meta template).
3. If selected, a destination address the staff member actually controls.

A cryptographically random URL token is generated in memory. Its SHA-256 digest, issuer, destination and one-hour expiration are stored in the `StaffInvitation` database row. The token is not logged or written to the database. The first GET stores a digest in a server session and immediately redirects to a token-free password form. The staff member chooses a password that must satisfy Django password validators. The row is locked and marked consumed in the same transaction that activates the account. Previously delivered links are invalid after activation or renewal. Recovery/access restrictions and existing privileged MFA continue to apply at sign-in. Users never receive admin, finance or cross-branch permissions merely by accepting a link.

Failed provider submissions leave a staff account **inactive**. The admin staff list shows invite state and an owner-only **Renew invitation** action. Providers report *acceptance*, which does not guarantee actual handset/inbox delivery.

## Provider configuration and costs

- SMS: reuses configured `SMS_ENABLED`, `SMS_PROVIDER` and the authorised Arkesel sender. Provider SMS/segments may incur existing charges.
- Email: reuses `KOFAD_EMAIL_PROVIDER` and `KOFAD_EMAIL_ENABLED` plus whichever verified Gmail API or Brevo HTTPS sender has been connected. Railway Hobby SMTP restrictions still apply; use the HTTPS options.
- WhatsApp: requires existing Meta Cloud connection (`WHATSAPP_ENABLED`, access token, number ID, app secret) and a **separately approved invitation template** with one URL placeholder, configured with `KOFAD_STAFF_INVITE_WHATSAPP_TEMPLATE`. Optional `KOFAD_STAFF_INVITE_WHATSAPP_LANGUAGE` defaults to `en`. Do not send invitations outside approved business messaging policies; service charges may apply.
- No new Railway service, paid upgrade, Google Workspace inbox or automatic email provider account is created by this feature.
- KOFAD's Gmail sender/OAuth consent and domain mailbox hosting are **separate pending setup**, see `docs/email-activation.md` and `docs/business-email.md`.

## Deployment checklist

1. Check pending PR against `railway-release` and verify there are no concurrent changes.
2. Run `ruff check .`, `python manage.py check`, `makemigrations --check --dry-run`, migrations, all Django tests and Playwright/browser-smoke (mobile widths 320, 390 and desktop).
3. Run tests for new Google signup, duplicate-verified-email collision, staff Google login restrictions, one-time invitation activation, expired/renewed token behaviour and provider-unavailable safeguards.
4. Review mobile light/dark contrast: staff sign-in, debt desk, Market Intelligence, catalog, customer account. WCAG minimum text contrast target is 4.5:1 for normal text (3:1 for large text). Visual checks must confirm, not assume, compliance.
5. Deploy with normal release process, verify web and existing worker health, plus phone-password and linked Google login regression.
6. Complete an invited-staff SMS test on a controlled, consented phone before general use. Test the email and WhatsApp paths **only after** those services are verified and configured. Do not send production tests to arbitrary people.

## Rollback

If necessary, roll back the Railway deployment to the prior known-good release. The new nullable customer phone and staff invitation tables are additive/backwards-compatible migrations; **do not delete customer or staff records or drop the new columns as a rollback shortcut**. Existing password logins and existing Google identities remain intact.
