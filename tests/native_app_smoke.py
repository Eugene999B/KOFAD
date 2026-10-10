"""Responsive KOFAD app downloads; staff distribution stays private."""
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
    raise RuntimeError("Do not run test fixtures against production.")

base = "http://127.0.0.1:8000"
out = Path("test-results")
out.mkdir(exist_ok=True)

with sync_playwright() as tool:
    browser = tool.chromium.launch()
    customer = browser.new_page(viewport={"width": 1440, "height": 900})
    for attempt in range(30):
        try:
            customer.goto(base + "/apps/")
            break
        except Exception:
            if attempt == 29:
                raise
            time.sleep(1)

    customer.get_by_role("heading", name="KOFAD on your device.").wait_for()
    assert customer.locator(".kf-download-device").count() == 3
    assert customer.locator(".kf-download-unavailable").count() == 2
    assert customer.get_by_role("link", name="Add as web app", exact=False).is_visible()
    assert customer.get_by_role("link", name="Open KOFAD Market").is_visible()
    assert "KOFAD Staff" not in customer.content()

    for platform in ("android", "apple", "windows"):
        icon = customer.locator(f'.kf-download-device img[src*="native-brands/{platform}"]')
        assert icon.count() == 1
        assert icon.evaluate("(image) => image.complete && image.naturalWidth > 0")

    customer.screenshot(path=str(out / "customer-native-app-desktop.png"), full_page=True)
    for width in (320, 390, 768):
        customer.set_viewport_size({"width": width, "height": 800})
        assert customer.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
        assert customer.locator(".kf-download-device").first.is_visible()
        if width == 390:
            customer.screenshot(path=str(out / "customer-native-app-mobile.png"), full_page=True)

    iphone = browser.new_page(viewport={"width": 390, "height": 844},
        device_scale_factor=3, is_mobile=True, has_touch=True,
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1")
    iphone.goto(base + "/apps/")
    iphone.get_by_role("heading", name="KOFAD on your device.").wait_for()
    assert iphone.locator(".kf-download-device").nth(2).get_attribute("data-native-platform") == "ios"
    assert iphone.evaluate("document.documentElement.scrollWidth <= innerWidth")
    iphone.screenshot(path=str(out / "customer-native-app-iphone.png"), full_page=True)
    iphone.close()

    customer.goto(base + "/")
    assert customer.get_by_role("link", name="Download the app").is_visible()
    customer.goto(base + "/market/")
    assert customer.locator(".native-market-promo").count() == 0
    assert customer.get_by_role("link", name="Install Market web app").count() == 0

    staff = browser.new_page(viewport={"width": 1280, "height": 900})
    staff.goto(base + "/staff/app/")
    assert "/login/" in staff.url
    browser.close()

print("KOFAD native download and market placement browser smoke OK")
