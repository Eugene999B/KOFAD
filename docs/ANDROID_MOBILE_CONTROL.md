# KOFAD Android app operations

## Where management happens

System administrators may open **Staff → Download staff app → Manage mobile apps** or directly visit `/staff/app/control/` on the official staff host. A valid superuser session is required. All edits go through the audited technical administration UI; ordinary staff cannot edit policies.

- **Mobile release policies:** one record for KOFAD Market (`customer`) and one for KOFAD Staff (`staff`). Set `minimum_android_version` only for a genuinely incompatible or security-critical installed version, and explain why. Leave it blank for ordinary updates.
- **Mobile notices:** separate public/general customer notices from general staff notices. Publish with the `enabled` checkbox, set expiry when appropriate. These feeds are **not authenticated**, so **never** include private staff names, operational secrets, order IDs, customer details, financial records or access links. Staff-specific private messages belong inside authenticated KOFAD email/approval/workspace systems.
- **Build and publication:** `GitHub → KOFAD → Actions → Build KOFAD Android APK` and `GitHub Releases` show Android releases. The publishing job requires a protected KOFAD signing key and real-device acceptance. Development/QA APKs are NOT approved customer downloads.
- **Website state:** `kofadimpex.com/apps/` shows real Android download only after a signed APK is publicly hosted and `KOFAD_CUSTOMER_APP_ANDROID_URL` and `KOFAD_CUSTOMER_APP_VERSION` are configured in Railway's `kofad-web` service. Never use a guessed URL.
- **Service monitoring:** use Railway production service health and KOFAD's existing dashboards for operational health. GitHub shows release build results and GitHub release download counters. Install counts, crashes, device inventory and background push delivery metrics are **not collected** by this feature.

## How updates work

The app checks a fixed, HTTPS, per-channel native version endpoint when opened and resumed. It NEVER accepts a download URL supplied by arbitrary remote content.

1. A **routine update** (cosmetic improvements, new optional features, small fixes) shows a dismissible banner linking to KOFAD's official update page.
2. A **mandatory update** is allowed only after a signed Android release is live, the published version is newer than the installed version, and the administrator sets a minimum supported version not higher than the published release. Older apps display a non-dismissable banner and block the app's native navigation while connected.
3. A connectivity failure does not incorrectly guess or enforce a missing release policy. This local gate cannot replace backend API compatibility enforcement for truly unsupported finance/inventory requests. The hosted secure services remain authoritative.
4. Never force merely because a version is large. Require a concrete security vulnerability or incompatibility. Test upgrades on real phones, including checkout redirects and offline/resume, before raising the minimum.

## Notices and permissions

Generic announcements appear in each channel's on-device inbox when the app checks for a new release. **OS notifications** are opt-in using Android's notification permission; an installed app can schedule local device alerts after it has retrieved new notices. There is **no background push delivery** when the app is closed: that will require an approved Firebase Cloud Messaging project, secure registration of devices to authenticated KOFAD accounts, revocation on logout, targeted delivery and delivery monitoring.

KOFAD uses the Android system share sheet for contact sharing without importing the address book. A photo or document attachment should use the operating system's picker when that attachment feature exists. Do **not** request blanket access to contacts, photos, microphone, location, camera or SMS without a real user-initiated feature and a clear reason. Android permissions can always be revoked in device settings.

## Publishing prerequisites (still required)

- Permanent KOFAD-owned release-signing keystore kept in protected GitHub environment secrets, with an offline encrypted backup in company custody.
- Signed APK passed signature, package/version and checksum checks, followed by real Android install/upgrade/security tests and explicit release approval.
- GitHub Releases public APK URL verified on a clean phone, then activated in Railway.
- No Play Store is required for this direct-download route. The visitor may have to approve the browser as an installation source.

This document is a release operations runbook, not proof that a public Android installer or real-time push infrastructure has been published.
