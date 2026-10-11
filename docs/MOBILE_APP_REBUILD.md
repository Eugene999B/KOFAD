# KOFAD Mobile: real app product plan

## Design standard and release gates

Two separately branded and independently signed applications must be developed:
- **KOFAD Market** (customer, `com.kofadimpex.market`): optional guest exploration, product detail, categories, favourites, shopping basket, authenticated checkout and payments, orders, returns, personal messages, notifications and account.
- **KOFAD Staff** (`com.kofadimpex.staff`): verified sign-in, branch/role selection, permission-scoped home, sales/receipts, inventory, online orders, approvals, finance reports and assigned communications. No staff-only data may be exposed via anonymous native feeds or persisted in plain local storage.

Current mobile feature release builds a true local native UI for first-run welcome, guest browsing, product detail, device-local saved items, account area and dedicated notices. It replaces the permanent oversized notification panel with a compact bell, and removes redirection from **public browsing**. It also standardizes show/hide password icons on KOFAD web login and recovery forms.

### What is still not fully native

It is **not yet** a fully authenticated standalone shopping or staff app. Protected checkout, customer orders and staff work modules continue to open the official, HTTPS, browser-mediated KOFAD account/workflow until KOFAD implements an authorized mobile session and API layer. Do not advertise these as finished native features.

### Next engineering phases, in order

1. **Secure mobile identity:** separate customer and staff sessions; short-lived tokens, refresh rotation and revocation, PKCE where applicable, role/store switching for staff, MFA support, sign-out, CSRF/session-boundary protections, device registration. Never reuse arbitrary browser cookies in the native shell.
2. **Native customer order journey:** authenticated cart APIs and price/stock validation on server; basket sync across devices; addresses; supported payment handoffs and verified return status; order history, cancellations/returns, support tickets. Guest checkout transitions through login without discarding basket.
3. **Native staff operations:** permission-scoped API read models and transactional commands for POS, stock, online orders, approvals, email and expenses; business rules/audit log enforced on the Django backend; predictable sync/offline handling for non-financial public data only. Do not queue money or approval actions offline.
4. **Notifications:** use a KOFAD-owned Firebase Cloud Messaging project for background Android push, securely bound to signed-in users and their scope, with device unregistration on logout, preference centre, retries and delivery/audit monitoring. Current local notifications only run after app fetches announcements and after users opt in.
5. **Native polish:** OS splash/icon, skeletons, gestures/back button/accessibility, dark/light mode, deep linking, performance, error states, real emulator and device acceptance suites, update policies and crash reporting.

### Android permissions

Use Android's **own** notification permission dialog only after the user taps to opt in. The app must never request blanket access to contacts, SMS, photos, camera or location during startup. For an attachment, prefer the Android system photo/file picker and ask for the least permission only when a feature needs it; cancel must not block unrelated features. Contacts may be shared using the OS share sheet without importing the address book.

### Release validation

- Check both native Android QA builds and a visual pass on multiple emulated screen sizes.
- Verify welcome gate, guest browsing, product detail, saved items, staff gates, update policies, permission denial and logout behavior; never use actual customer financial accounts in shared emulators.
- Confirm password toggle remains button type=button, accessible, hidden by default and does not interfere with form submission.
- Public APK button activation remains subject to separate signed release, actual device testing and verification. Never substitute a debug APK.
