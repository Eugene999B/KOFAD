# KOFAD Google Sign-In + Gmail API activation

## What is installed

- Google OAuth2/OIDC login with independent staff and customer routes.
- **Explicit linking only**: staff sign-in is limited to existing staff accounts;
  customer sign-in is limited to phone-verified existing customer accounts.
- Google account identity is keyed by Google's immutable `sub`, not a user-
  editable profile email. Linking and unlinking require account password.
- State, nonce, PKCE, signed JWT/issuer/audience/expiry/email verification,
  limited request lifetime and existing staff MFA/security requirements.
- Staff and customers can still sign in with their existing phone/password.
- HTTPS Gmail API, with `gmail.send` scope only, authorised by company manager.
  Refresh consent is encrypted in PostgreSQL using `DJANGO_SECRET_KEY`.
- Email verification, customer order notifications and authorised staff online
  payment alerts use the existing `kofad-sms` worker every 60 seconds. **No
  additional Railway service or cron is necessary.**
- All switches are off until authorised Google credentials are set.

**Important:** Railway Hobby blocks SMTP ports. Do not configure SMTP or upgrade
to Pro for this workflow. The Gmail API sends over HTTPS port 443.

## Step 1: Google Cloud project for user sign-in

1. Open https://console.cloud.google.com/auth/overview and select/create a
   company-owned Google Cloud project.
2. Configure its **Branding** (app name KOFAD IMPEX ENTERPRISE, support email
   owned by the company) and **Audience**. Add testers when in testing mode.
3. Create an OAuth 2.0 **Web application** client. Under **Authorized redirect
   URIs** enter both exact HTTPS destinations:

   - `https://staff.kofadimpex.com/auth/google/staff/callback/`
   - `https://market.kofadimpex.com/market/auth/google/callback/`

4. Use scopes `openid` and `email`. There is no need for Gmail scopes for
   staff/customer Google sign-in.
5. Save the client ID and secret in **Railway secure service variables**,
   not source control. On **kofad-web** set:

   - `KOFAD_GOOGLE_CLIENT_ID=<web client id>`
   - `KOFAD_GOOGLE_CLIENT_SECRET=<web client secret>`
   - `KOFAD_GOOGLE_OAUTH_ENABLED=1`

6. Redeploy `kofad-web` if Railway does not do so automatically. Sign in
   normally, go to **My account** or **Customer account security**, enter
   your current password, and link Google. Then sign out and test Google login.
   The Google button is intentionally hidden until the service is configured.

## Step 2: separate Google Cloud app for the business Gmail sender

A separate Google Cloud project/client isolates sensitive Gmail sending
authorization from the public-facing Google login consent screen.

1. Select/create a second company-controlled Google Cloud project, enable
   **Gmail API**, set Branding and Audience/test users, and request the
   `https://www.googleapis.com/auth/gmail.send` scope (plus `openid email`).
2. Create an OAuth 2.0 **Web application** client. Its exact redirect URI:

   - `https://staff.kofadimpex.com/auth/google/gmail/callback/`

3. Google may require app verification for Gmail scopes before broad production
   use. External consent apps in testing can have short-lived refresh tokens;
   test mode is not a permanent production sender setup.
4. Save the sender's client ID/secret securely on **both** `kofad-web`
   and `kofad-sms`, sharing the same values:

   - `KOFAD_GMAIL_CLIENT_ID=<sender web client id>`
   - `KOFAD_GMAIL_CLIENT_SECRET=<sender web client secret>`
   - `KOFAD_GMAIL_API_ENABLED=1`
   - `KOFAD_EMAIL_ENABLED=1`

5. Open KOFAD's private staff **My account** as a company manager. Press
   **Connect business Gmail securely**, enter your KOFAD password and approve
   Google's send-only access while signed in to the company sender mailbox.
   No Gmail password enters KOFAD or this chat.
6. Press **Send a test email**; confirm the original Gmail inbox received it.
   Test email verification for an existing staff and customer account.
7. Enable `KOFAD_EMAIL_NOTIFICATIONS_ENABLED=1` on **both** services once
   delivery works. Users opt in individually; only verified, consented
   addresses receive automatic notices.
8. Test a small order, verify at most one queued notification and review
   delivery. Gmail quota/rate limits, delivery reputation, and SPF/DKIM/DMARC
   for custom-domain mailboxes still require operational monitoring.

## Security, rollback and cost notes

- No secrets or tokens are stored in GitHub, templates or regular log output.
- Never paste client secrets/refresh tokens into ChatGPT or a public issue.
- Revoking Gmail from KOFAD's account screen deletes the stored encrypted token;
  revoke access from the corresponding Google Account's security settings too.
- Rotating Django `DJANGO_SECRET_KEY` invalidates encrypted Gmail sender tokens;
  reconnect Gmail after a secret-key rotation.
- No Google account automatically creates a staff profile or customer account.
  Customers first create a KOFAD account and verify their Ghana phone via SMS.
- Password/phone login remains the fallback even if Google's API is unavailable.
- Gmail API HTTP requests use outbound HTTPS and are compatible with Railway Hobby.
- Gmail send permission belongs only to an authorised company manager's
  designated mailbox, not each individual customer.
- The outbox uses at-least-once sending; an interrupted acknowledgment can
  occasionally lead to a duplicated email. Do not use this to authorize payments.
- A Google OAuth client/consent setup cannot be completed by the ChatGPT Gmail
  connector, because that connector's authorization is separate from the
  KOFAD server's Google Cloud project.
