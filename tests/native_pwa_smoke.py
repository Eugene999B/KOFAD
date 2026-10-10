"""Check real Chromium registration and responsive Android/Windows PWA install UX."""
import os
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.conf import settings
if not settings.DEBUG:
    raise RuntimeError("Never run browser fixture tests against production.")

base = "http://127.0.0.1:8000"
shots = Path("test-results")
shots.mkdir(exist_ok=True)
with sync_playwright() as tool:
    browser = tool.chromium.launch()
    android = browser.new_page(
        viewport={"width": 390, "height": 844},
        device_scale_factor=2, is_mobile=True, has_touch=True,
        user_agent=("Mozilla/5.0 (Linux; Android 15; Pixel 9) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/129.0.0.0 Mobile Safari/537.36"),
    )
    for attempt in range(30):
        try:
            android.goto(base + "/market/app/install/")
            break
        except Exception:
            if attempt == 29:
                raise
            time.sleep(1)
    android.get_by_role("heading", name="KOFAD, wherever you are.").wait_for()
    assert android.locator('link[rel="manifest"]').get_attribute("href") == "/market/app/manifest.webmanifest"
    manifest = android.evaluate('''async () => {
      const response = await fetch("/market/app/manifest.webmanifest");
      if (!response.ok) throw Error("Manifest status: " + response.status);
      return response.json();
    }''')
    assert manifest["display"] == "standalone", manifest
    assert manifest["start_url"] == "/market/"
    assert len(manifest["icons"]) == 2
    assert android.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    registration = android.evaluate('''async () => {
      const reg = await navigator.serviceWorker.ready;
      return {scope: reg.scope, script: reg.active?.scriptURL};
    }''')
    assert registration["scope"].endswith("/market/"), registration
    assert registration["script"].endswith("/market/app/sw.js"), registration
    assert android.locator(".kf-download-device").count() == 3
    android.get_by_role("button", name="Install for Android").click()
    assert android.get_by_text("In Chrome, open the browser menu", exact=False).is_visible()
    android.screenshot(path=str(shots / "market-android-web-install.png"), full_page=True)

    desktop = browser.new_page(viewport={"width": 1280, "height": 900})
    desktop.goto(base + "/market/app/install/")
    desktop.get_by_role("heading", name="KOFAD, wherever you are.").wait_for()
    assert desktop.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    desktop.screenshot(path=str(shots / "market-windows-web-install.png"), full_page=True)
    assert desktop.locator(".kf-download-device").count() == 3
    assert desktop.locator(".kf-download-device").evaluate_all("(items) => items.length === 3")
    desktop.get_by_role("button", name="Install desktop app").click()
    assert desktop.get_by_text("Install this site as an app", exact=False).is_visible()
    iphone = browser.new_page(
        viewport={"width": 390, "height": 844}, device_scale_factor=3,
        is_mobile=True, has_touch=True,
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
    )
    iphone.goto(base + "/market/app/install/?device=iphone")
    iphone.get_by_role("heading", name="KOFAD, wherever you are.").wait_for()
    assert iphone.get_by_text("In Safari, choose", exact=False).is_visible() or iphone.get_by_text("Open this page in Safari", exact=False).is_visible()
    assert iphone.get_by_role("button", name="Add to Home Screen").is_visible()
    assert iphone.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    iphone.screenshot(path=str(shots / "market-iphone-home-screen-guide.png"), full_page=True)
    iphone.close()

    downloads = browser.new_page(viewport={"width": 390, "height": 844})
    downloads.goto(base + "/apps/")
    assert downloads.get_by_role("link", name="Install KOFAD Market", exact=False).is_visible()
    assert downloads.locator(".kf-download-device").count() == 3
    assert downloads.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    downloads.screenshot(path=str(shots / "market-real-app-available-vs-native-pending.png"), full_page=True)
    browser.close()
print("KOFAD browser-installable Android/Windows PWA manifests, worker and page smoke OK")
