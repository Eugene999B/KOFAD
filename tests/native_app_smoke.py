"""Independent public vs authenticated staff native-app download UI smoke (CI)."""
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
    raise RuntimeError("Do not run browser test fixtures against a production server.")

base = "http://127.0.0.1:8000"
out = Path("test-results")
out.mkdir(exist_ok=True)
with sync_playwright() as browser_tool:
    browser = browser_tool.chromium.launch()
    customer = browser.new_page(viewport={"width": 1440, "height": 900})
    for attempt in range(30):
        try:
            customer.goto(base + "/apps/")
            break
        except Exception:
            if attempt == 29:
                raise
            time.sleep(1)
    customer.get_by_role("heading", name="KOFAD, wherever you are.").wait_for()
    assert customer.locator(".kf-download-device").count() == 3
    assert customer.get_by_role("link", name="Install KOFAD Market", exact=False).is_visible()
    assert customer.get_by_role("link", name="Open Market", exact=False).is_visible()
    for platform in ("android", "apple", "windows"):
        icon = customer.locator(f".kf-download-device img[src*=\\'native-brands/{platform}\\']")
        assert icon.count() >= 1
        assert icon.first.evaluate("(img) => img.complete && img.naturalWidth > 0")
    assert "KOFAD Staff" not in customer.content()
    customer.screenshot(path=str(out / "customer-native-app-desktop.png"), full_page=True)
    for width in (320, 390, 768):
        customer.set_viewport_size({"width": width, "height": 800})
        assert customer.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), f"Customer download layout overflow: {width}"
        assert customer.locator(".kf-download-device").first.is_visible()
        if width == 390:
            customer.screenshot(path=str(out / "customer-native-app-mobile.png"), full_page=True)

    iphone = browser.new_page(
        viewport={"width": 390, "height": 844},
        device_scale_factor=3, is_mobile=True, has_touch=True,
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    )
    iphone.goto(base + "/apps/")
    iphone.get_by_role("heading", name="KOFAD, wherever you are.").wait_for()
    assert iphone.locator(".kf-download-device").first.get_attribute("data-native-platform") == "ios"
    assert iphone.get_by_role("link", name="Add to Home Screen", exact=False).is_visible()
    assert iphone.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "iPhone horizontal overflow"
    iphone.screenshot(path=str(out / "customer-native-app-iphone.png"), full_page=True)
    iphone.close()

    customer.goto(base + "/")
    assert customer.get_by_role("link", name="Install KOFAD web app", exact=False).is_visible()
    customer.goto(base + "/market/")
    assert customer.get_by_role("link", name="Install the web app", exact=False).is_visible()
    staff = browser.new_page(viewport={"width": 1280, "height": 900})
    staff.goto(base + "/staff/app/")
    assert "/login/" in staff.url
    staff.get_by_label("Username, mobile number or verified email", exact=True).fill("admin")
    # Existing browser suite rotates the isolated CI admin password first.
    staff.get_by_label("Password", exact=True).fill("New-private-admin-passphrase-986!")
    staff.get_by_role("button", name="Sign in", exact=False).click()
    staff.wait_for_url(base + "/workspace/")
    staff.get_by_role("link", name="Download staff app", exact=True).click()
    staff.get_by_role("heading", name="Work from anywhere.").wait_for()
    assert staff.locator(".kf-download-device").count() == 3
    assert staff.get_by_role("button", name="Use on iPhone", exact=False).is_visible()
    assert staff.get_by_role("link", name="Open workspace").is_visible()
    assert "KOFAD Staff" in staff.content()
    staff.screenshot(path=str(out / "staff-native-app-desktop.png"), full_page=True)
    for width in (320, 390, 768):
        staff.set_viewport_size({"width": width, "height": 900})
        assert staff.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), f"Staff download layout overflow: {width}"
        if width == 390:
            staff.screenshot(path=str(out / "staff-native-app-mobile.png"), full_page=True)
    browser.close()
