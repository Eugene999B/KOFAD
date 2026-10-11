# KOFAD Mobile APIs — authoritative architecture and delivery sequence

KOFAD **owns its mobile APIs**. They are new, versioned HTTPS endpoints in the existing Django + PostgreSQL backend; there is no third-party API to purchase. The native Android/iOS/Windows clients consume structured JSON and stay on their own screens. Existing stock, retail/wholesale pricing, reservations, audit trail, OTP, MFA, branch permissions and verified payment webhooks remain the business authority.

## Available in the first versioned foundation

- `GET https://market.kofadimpex.com/market/app/catalog.json` — existing public paginated product feed.
- `GET https://market.kofadimpex.com/market/mobile/v1/bootstrap/` — version and *truthful* feature availability.
- `GET https://market.kofadimpex.com/market/mobile/v1/products/{id}/` — enabled-only public listing, server price/availability snapshot, bounded descriptions, images, highlights and tags. Native product detail view can consume this directly.
- `GET https://staff.kofadimpex.com/staff/mobile/v1/bootstrap/` — existing **browser-session-only** staff scope and allowed modules, limited to active verified user, MFA, current assigned branch. Returns 401/403 for unauthenticated or unauthorized access; no staff data is included in public endpoints.

No write endpoints or native bearer tokens are issued at this stage. In particular, an Android APK cannot silently use browser sessions; cookies from external sign-in must not be copied into a WebView. These initial endpoints do **not** by themselves constitute the full native app.

## Critical next step: secure KOFAD Mobile Identity

Before enabling mobile accounts or any write API:

1. Introduce dedicated OAuth 2.1-style authorization-code + PKCE (S256) using a reviewed authorization server or OAuth library with restricted native client identifiers, registered app/deep-link return destinations and CSRF state. Never put a reusable password or refresh token in a URL.
2. Start login in the OS's trusted system browser. Continue using the existing verified KOFAD customer phone/password/Google/OTP paths; staff must complete their existing staff login, forced-password-change and MFA policy. Bind the authorization grant to the exact customer or staff principal and current role/branch access.
3. Exchange a **single-use, short-lived authorization code** with the PKCE verifier. Issue short-lived, audience- and channel-bound access tokens with hashed, rotating refresh-token records; persist secrets only in Android Keystore / iOS Keychain / OS-protected desktop storage. Enforce device/session revocation, logout, credential/session-version changes, abuse throttles and expiry.
4. Reauthorize server-side on every call: customer resource ownership, staff role + branch + MFA freshness. No staff app data may be exposed via the anonymous catalogue origin or native build contents. Add audit logging to every business mutation. A user must be able to revoke sessions/devices.

## Native Market endpoint backlog (behind authentication)

- `GET /market/mobile/v1/me/`, `POST /market/mobile/v1/sessions/revoke/`: identity, profile, saved addresses, device sign-out.
- `GET/PUT /market/mobile/v1/cart/`: customer-linked cart synchronized across devices. Server recalculates prices, units, stock and discounts; never trust amounts supplied by the phone.
- `GET /market/mobile/v1/checkout/quote/`, `POST /market/mobile/v1/orders/`: fulfilment/delivery quote and idempotent server-created order with reservation. Signed-in user is mandatory; no purchase is committed by tapping an offline button.
- `GET /market/mobile/v1/orders/`, `GET /market/mobile/v1/orders/{id}/`: only the authenticated customer's orders, paginated with server filtering and payment status verified server-side.
- `POST /market/mobile/v1/orders/{id}/payment-intent/`: Paystack/Hubtel safe handoff; backend owns payment credentials, references, callbacks, verification and financial ledger posting.
- `GET/POST /market/mobile/v1/support/`, `GET/POST /market/mobile/v1/returns/`: account-owned support/return workflows with bounded uploads and audit controls.
- `GET/PUT /market/mobile/v1/wishlist/`: authenticated sync; local guest favourites stay guest-only until explicit merge.

## Native Staff endpoint backlog (behind stronger authorization)

- Scoped staff home, own tasks and unread counts (server picks permitted branch).
- Inventory catalogue/stock and permitted adjustments with dual approvals when required.
- POS trade drafts and idempotent authorized sales using existing accounting/inventory transaction services.
- Online order queues and permission-scoped fulfilment actions.
- Approval inbox and actions, audit log, manager notifications; enforced role, branch and second-factor recency.
- Assigned email centre, support and expense/finance modules with independent permission gates.
- Never cache financial, customer identity or HR information as unauthenticated offline content.

## Notifications and OS capabilities

- Android Firebase Cloud Messaging + APNs for iOS, bound to authenticated devices, topic/scope protection, preferences, retries, per-device revocation on logout. Current local notice alerts are not full background push.
- Only request **POST_NOTIFICATIONS** after contextual opt-in. Use Android's system photo/file picker for attachments without broad gallery or contacts permissions. Camera, contacts, location or microphone prompts occur only for implemented user-initiated workflows.
- Start with a small bottom-tab layout, a real app session, secure login/guest handoff, consistent loading/offline states, accessibility and deep links. The customer and staff apps remain separately signed and shipped.
- Feature contract must remain truthful; client buttons cannot claim that unimplemented native endpoints exist. Keep public signed APK releases behind real-device test and compatibility checks.

## Quality gates

Run the versioned API tests (pagination/ownership/active-only, sensitive-field denial, CORS, host-routing, branch/MFA, rate limit, idempotency, payment webhook safety), both Android QA builds, mocked network failures and emulator user journeys. Do not merge sensitive write APIs without security review and migration/recovery validation. Keep the old production business workflows available until equivalent native transactions are verified.
