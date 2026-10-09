"""Isolated Chromium/real-viewport acceptance for the Export Studio (CI only)."""
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
    raise RuntimeError("Export Studio browser smoke is only permitted on local CI fixtures.")

origin = "http://127.0.0.1:8000"
out = Path("test-results")
out.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 980}, device_scale_factor=1)
    failures = []
    page.on("pageerror", lambda e: failures.append(str(e)))
    for attempt in range(30):
        try:
            page.goto(origin + "/" + settings.STAFF_LOGIN_SLUG + "/")
            break
        except Exception:
            if attempt == 29:
                raise
            time.sleep(1)
    cookie_button = page.locator("[data-cookie-accept]")
    if cookie_button.is_visible():
        cookie_button.click()
    page.get_by_label("Username, mobile number or verified email").fill("admin")
    page.get_by_label("Password", exact=True).fill("New-private-admin-passphrase-986!")
    page.get_by_role("button", name="Sign in", exact=False).click()
    page.wait_for_url(origin + "/workspace/")
    page.goto(origin + "/exports/")
    page.get_by_role("heading", name="Create a download").wait_for()
    assert page.get_by_text("Registered Market customer accounts").count() > 0
    assert page.locator(".export-library-item").count() >= 10
    page.locator("#export-library-query").fill("gateway")
    assert page.locator(".export-library-item:visible").count() >= 1
    assert page.locator(".export-library-item:visible").count() < 6
    page.locator("#export-library-query").fill("")
    assert page.locator(".export-library-item:visible").count() >= 10
    assert page.locator(".export-formats button").count() == 4
    page.screenshot(path=str(out / "export-studio-desktop.png"), full_page=True)
    for width in (320, 390, 768):
        page.set_viewport_size({"width": width, "height": 900})
        page.locator("#export-dataset").scroll_into_view_if_needed()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), f"Export Studio horizontal overflow at {width}px"
        assert page.locator("#export-dataset").is_visible()
        assert page.locator(".export-formats button").first.is_visible()
        if width == 390:
            page.screenshot(path=str(out / "export-studio-mobile.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 980})
    page.locator("#export-dataset").select_option("market_customers")
    assert "latest available records" in page.locator("#export-window-note").inner_text()
    with page.expect_download(timeout=30000) as csv_download:
        page.get_by_role("button", name="Raw data export", exact=False).click()
    data = csv_download.value.path().read_bytes().decode("utf-8-sig")
    assert "Registered name" in data and "Phone verification" in data
    assert csv_download.value.suggested_filename.endswith(".csv")
    page.locator("#export-dataset").select_option("sales_lines")
    assert "selected start and end" in page.locator("#export-window-note").inner_text()
    with page.expect_download(timeout=30000) as spreadsheet:
        page.get_by_role("button", name="Excel workbook", exact=False).click()
    assert spreadsheet.value.suggested_filename.endswith(".xlsx")
    assert not failures, failures
    browser.close()
