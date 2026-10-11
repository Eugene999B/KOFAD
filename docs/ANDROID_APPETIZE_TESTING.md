# Test KOFAD Android on an iPhone — Appetize upload without file picker

iPhone Safari may grey out `.apk` files even when the Appetize Apps dashboard displays its Android filter. **Do not rename the APK to .zip** or upload the publishing bundle. Appetize supports direct Android APK upload from its GitHub Action, bypassing Safari file selection.

## One-time setup on your iPhone

1. In Appetize, open **Organization > API Token** and create/retrieve a token as an authorized organization administrator. Treat it like a password.
2. In GitHub open [KOFAD > Settings > Secrets and variables > Actions](https://github.com/Eugene999B/KOFAD/settings/secrets/actions), choose **New repository secret**, name it `APPETIZE_API_TOKEN` and paste the token **there only**. Do not send it to ChatGPT, place it in a repository file, or share screenshots showing it.
3. Go to [KOFAD > Actions](https://github.com/Eugene999B/KOFAD/actions) and choose **Upload KOFAD Market Android QA to Appetize**.
4. Select **Run workflow** on branch `railway-release`, leaving the source QA run ID default `38054211091` or entering the ID of a newer successful "Verify native KOFAD clients (QA only)" run.
5. When green, open the workflow run **Summary** and tap the Appetize emulator link. Test KOFAD on a virtual Android device directly from Safari on your iPhone.

No physical Android phone, Google Play, keystore, App Store build or Windows computer is required for this initial emulator run. This workflow uploads an existing **debug-only** QA build from a successful GitHub Actions run, checks its package identity and APK signature, and sends it only to your Appetize organization using the token stored as a GitHub secret. It does not publish the APK on the KOFAD customer download page, does not grant Appetize access to signing secrets, and does not create any public installer.

If Appetize returns an upload or processing error, inspect the run's step outcome and avoid exposing the secret value when seeking assistance. For security, test browsing/catalog and navigation first; do not use real payment cards or sensitive employee logins in a shared emulator.
