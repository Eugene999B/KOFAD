# KOFAD native client release plan

**Status:** distribution + native project foundations only. **No release APK/AAB, IPA, App Store record, Play Store record, or signed Windows installer has been built, reviewed or published.** The website must never indicate otherwise. Do not turn these links on merely to test a draft.

## Separate applications

| | Customer (public) | Staff (private) |
|---|---|---|
| Marketing | `https://kofadimpex.com/apps/`, homepage, Market | Authenticated staff sidebar only |
| Android package | `com.kofadimpex.market` | `com.kofadimpex.staff` |
| iOS bundle ID | `com.kofadimpex.market` | `com.kofadimpex.staff` |
| Windows identity | `com.kofadimpex.market.windows` | `com.kofadimpex.staff.windows` |
| Allowed business origin | `https://market.kofadimpex.com/` | `https://staff.kofadimpex.com/` |
| Windows data folder | `KOFAD-Market` | `KOFAD-Staff` |
| Release update feed | `https://downloads.kofadimpex.com/windows/customer/` | `https://downloads.kofadimpex.com/windows/staff/` |
| Version endpoint | `/market/app/releases.json` | `/staff/app/releases.json` (staff login required) |

The website's download cards are active only after approved URLs have been configured for a platform. **Never place an unsigned APK/EXE, debug APK, expired store listing or placeholder URL in production environment variables.** App binaries and update feeds must be served over HTTPS by KOFAD and signed with the appropriate platform identity.

## Website settings

After publishing a verified platform artifact, set on Railway web service **only** the relevant variables:

```text
KOFAD_CUSTOMER_APP_VERSION=1.0.0
KOFAD_CUSTOMER_APP_ANDROID_URL=https://play.google.com/store/apps/details?id=com.kofadimpex.market
KOFAD_CUSTOMER_APP_IOS_URL=https://apps.apple.com/.../id<real-id>
KOFAD_CUSTOMER_APP_WINDOWS_URL=https://downloads.kofadimpex.com/windows/customer/<signed-setup>.exe

KOFAD_STAFF_APP_VERSION=1.0.0
KOFAD_STAFF_APP_ANDROID_URL=https://play.google.com/store/apps/details?id=com.kofadimpex.staff
KOFAD_STAFF_APP_IOS_URL=https://apps.apple.com/.../id<real-id>
KOFAD_STAFF_APP_WINDOWS_URL=https://downloads.kofadimpex.com/windows/staff/<signed-setup>.exe
```

Actual domain ownership, working links, publisher certificate validity and binary integrity must be independently checked before setting values. The validator prevents obvious fake or insecure schemes; it does **not** cryptographically verify a URL's target.

## Android and iOS project generation

The `native/mobile` directory contains a Capacitor 7 base scaffold for **two separate packages**. The selected channel changes the app ID and permitted start domain.

1. Set up **separate build directories/checkouts for each app/channel**, rather than switching an already-generated Android or Xcode project between customer and staff. Once generated, native bundle IDs are stored in Gradle/Xcode and cannot safely be switched by editing only `capacitor.config.ts`.
2. On each new checkout, `cd native/mobile && npm install` and run `npm run prepare:customer` or `npm run prepare:staff`.
3. Generate real Android and iOS projects using `npx cap add android` (Android Studio) and `npx cap add ios` (macOS/Xcode), **with the matching channel selected for the configuration**.
4. Complete native work before production: dedicated native screen(s) offering meaningful platform-native functionality; safe deep-link/Universal Link handling for Google login and payment gateway returns; reliable account/session isolation; offline UX and empty-state recovery; app-specific icons and splash assets; Play Integrity and iOS Keychain integration if needed; accessibility and large-text/device rotation checks.
5. Android: create a **release-signed AAB** in Android Studio with an organization-owned protected Play signing account. Publish through Google Play internal/closed testing first, then production when approved. Update flow: official Play app updates.
6. iOS: Apple Developer membership, App Store Connect, signing team, privacy disclosures, device/Simulator QA, TestFlight, and App Review. Update flow: iOS App Store versioning. **Apple 4.2 rejects a repackaged website without meaningful native value**; this scaffold is intentionally **not App-Store-ready**.
7. Do not deploy remote content or use third-party HTML as a privileged native bridge with unrestricted plugins. Staff financial workflows and customer card/MoMo transactions need platform-specific acceptance before approval.

Capacitor `server.url` currently points to the existing HTTPS backend to preserve behavior while native frontends are developed. Capacitor documents this as intended for live reload, not a full native production strategy. It is a **transition build** and should not be advertised as a completed high-quality native storefront or staff client until native UI and secure login/payment handoff are implemented. The local `www/index.html` is a packaged fallback, not a replacement for offline-safe server transactions.

## Windows desktop project

`native/desktop` implements a genuine installable Electron Windows client framework. The main process runs with **sandbox + context isolation + no Node integration, no webview, no privileged preload/IPC**, allowlisted HTTPS destinations, denied permissions, and browser handoff of controlled OAuth/checkout hosts. Customer and staff have separate product IDs, data directories, navigation policy and update feeds.

On a Windows build machine, install Node LTS and run:
```powershell
cd native/desktop
npm install
npm run test
npm run dev:customer
# Or npm run dev:staff
```

For an installer, register the release publisher and provide **secret signing material through protected CI or credential vault**, never Git:
```text
WIN_CSC_LINK                   # certificate file/secure reference
WIN_CSC_KEY_PASSWORD           # certificate passphrase
KOFAD_WINDOWS_CERT_SUBJECT     # exact certified signing identity
```
With the relevant protected secrets configured and an approved Windows release channel, run `npm run dist:customer` or `npm run dist:staff`. The script **fails closed** when signing material is absent. Two separate release folders/feeds are mandatory. Configure the updater feed on a separately verified `downloads.kofadimpex.com` release origin before shipping.

The updater checks only published/packaged client builds. It presents a user choice to **download**, then a second choice to **install and restart**. It must never silently replace an app during an active POS operation. Code signing and update-manifest verification must remain on; do not ship self-signed, untrusted public EXEs or disable update signature verification.

## Acceptance and deployment gates

- [ ] Native mobile apps generated, compiled and installed on physical Android and iPhones
- [ ] Windows customer and staff installers signed, publisher checked and installed on clean Windows device
- [ ] Customer/Staff app packages have distinct storage, signed IDs, store records and update destinations
- [ ] Real Google SSO/OAuth handoff and return URI tests; no WebView user-agent block
- [ ] Real Paystack/Hubtel MoMo and external payment redirect/resume tests; no double posting
- [ ] Screen-lock/reopen, app suspend/resume, network loss, retry, large files, uploads/downloads, background mode tests
- [ ] No customer/staff token sharing, secret leakage, native untrusted redirect or cross-channel update
- [ ] Android Play closed-testing and Apple TestFlight feedback, publisher review, privacy/data deletion requirements
- [ ] Customer and staff update prompts tested **with signed upgrades and rollback plan**
- [ ] KOFAD existing backend PostgreSQL, browser regression and finance UAT passed
- [ ] Secure public verified link and release version configured only after publication
- [ ] Review failure recovery and accessibility on small Android, iPhone and Windows screen sizes

Native app project versions and download URLs are **NOT** evidence of live app availability. Signing and release activities require authorized owner developer accounts. Do not automate public release or post any credentials in the repo.
