"""Remote browser evidence: real login, navigation, checkout and narrow-screen overflow."""
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

# Isolated CI fixtures only; never run this script against production.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.conf import settings
from django.contrib.auth.models import User
from core.models import Branch
if not settings.DEBUG:
    raise RuntimeError("Browser fixtures are forbidden outside DEBUG environments.")
warehouse, _ = Branch.objects.get_or_create(code="browser-wh", defaults={"name": "Browser warehouse"})
User.objects.get(username="demo").access.branches.add(warehouse)

out = Path("test-results")
out.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width":1440,"height":1000}, device_scale_factor=1)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    for attempt in range(30):
        try:
            page.goto("http://127.0.0.1:8000/login/")
            break
        except Exception:
            time.sleep(1)
    page.screenshot(path=str(out / "login-desktop.png"), full_page=True)
    assert page.get_by_text("Private setup key", exact=True).count() == 0
    assert page.locator('link[rel="icon"]').get_attribute("href").endswith(".png")
    page.get_by_role("button", name="Show password", exact=True).click()
    assert page.locator("#password").get_attribute("type") == "text"
    page.get_by_role("button", name="Hide password", exact=True).click()
    assert page.locator("#password").get_attribute("type") == "password"
    page.set_viewport_size({"width":390,"height":844})
    page.screenshot(path=str(out / "login-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.get_by_role("button", name="Sign in", exact=False).bounding_box()["y"] < 700
    for width in (320, 768):
        page.set_viewport_size({"width":width,"height":900})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "login overflow"
    page.set_viewport_size({"width":1440,"height":1000})
    page.get_by_role("link",name="Forgot password?",exact=True).click()
    page.get_by_role("heading",name="Forgot your password?",exact=True).wait_for()
    assert page.get_by_text("SMS recovery is not available yet.",exact=False).is_visible()
    page.goto("http://127.0.0.1:8000/login/")
    page.get_by_label("Username", exact=True).fill("demo")
    page.get_by_label("Password", exact=True).fill("isolated-demo-browser-password")
    page.get_by_role("button", name="Sign in", exact=False).click()
    page.wait_for_url("http://127.0.0.1:8000/")
    page.get_by_role("heading",name="Command centre",exact=True).wait_for()
    page.screenshot(path=str(out / "dashboard-desktop.png"), full_page=True)
    page.goto("http://127.0.0.1:8000/sales/new/")
    page.locator(".product-card").first.get_by_role("button",name="Add",exact=False).click()
    page.locator("#product-query").fill("Classic leather")
    page.locator("#catalog-search").get_by_role("button",name="Search",exact=True).click()
    page.locator(".product-card").filter(has_text="Classic leather sandals").get_by_role("button",name="Add",exact=False).click()
    assert page.locator("#cart-count").inner_text() == "2 lines"
    page.get_by_role("button",name="Fill exact cash amount").click()
    lost_response = []
    def lose_confirmed_response(route):
        response = route.fetch()
        assert response.ok
        lost_response.append(response.json())
        route.abort()
    page.route("**/api/trades/",lose_confirmed_response)
    page.get_by_role("button",name="Complete sale",exact=False).click()
    page.locator("#pos-error").wait_for(state="visible")
    assert page.locator("#party").is_disabled()
    page.unroute("**/api/trades/",lose_confirmed_response)
    page.once("dialog",lambda dialog: dialog.accept())
    page.reload()
    assert page.locator("#cart-count").inner_text() == "2 lines"
    page.get_by_role("button",name="Complete sale",exact=False).click()
    page.wait_for_url("**/documents/**/")
    assert page.url.endswith(lost_response[0]["url"])
    page.screenshot(path=str(out / "receipt-desktop.png"), full_page=True)
    page.goto("http://127.0.0.1:8000/sales/new/")
    page.screenshot(path=str(out / "pos-desktop.png"), full_page=True)
    for path in ["/","/inventory/","/finance/","/operations/","/reports/","/communications/"]:
        page.goto("http://127.0.0.1:8000"+path)
        assert page.locator("h1").count() > 0
    page.set_viewport_size({"width":390,"height":844})
    for name,path in [("dashboard","/"),("pos","/sales/new/"),("inventory","/inventory/")]:
        page.goto("http://127.0.0.1:8000"+path)
        page.screenshot(path=str(out / (name+"-mobile.png")), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), path + " overflows"
    page.get_by_role("button",name="Toggle navigation",exact=True).click()
    assert page.locator("#menu-toggle").get_attribute("aria-expanded") == "true"
    page.keyboard.press("Escape")
    assert page.locator("#menu-toggle").get_attribute("aria-expanded") == "false"
    page.get_by_role("button",name="Toggle navigation",exact=True).click()
    page.get_by_role("button",name="Close menu",exact=True).click()
    assert page.locator("#menu-toggle").get_attribute("aria-expanded") == "false"
    page.goto("http://127.0.0.1:8000/sales/new/")
    page.locator(".product-card").first.get_by_role("button",name="Add",exact=False).click()
    assert page.locator("#mobile-cart-count").inner_text() == "1"
    page.locator("#cart-jump").click()
    assert page.locator("#checkout-panel").evaluate("el => el === document.activeElement")
    page.locator("#cart").get_by_role("button",name="Remove",exact=False).click()
    for width in (320, 768):
        page.set_viewport_size({"width":width,"height":900})
        for path in ("/", "/sales/new/", "/inventory/", "/finance/"):
            page.goto("http://127.0.0.1:8000"+path)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), str(width)+path
    page.set_viewport_size({"width":390,"height":844})
    admin_page = browser.new_page(viewport={"width":1280,"height":900})
    admin_page.goto("http://127.0.0.1:8000/login/")
    admin_page.get_by_label("Username",exact=True).fill("admin")
    admin_page.get_by_label("Password",exact=True).fill("ADMIN")
    admin_page.get_by_role("button",name="Sign in",exact=False).click()
    admin_page.wait_for_url("http://127.0.0.1:8000/")
    admin_page.get_by_role("heading",name="Command centre",exact=True).wait_for()
    admin_page.screenshot(path=str(out / "admin-direct-login.png"),full_page=True)
    admin_page.goto("http://127.0.0.1:8000/account/")
    admin_page.get_by_label("Recovery phone number",exact=True).fill("0241234567")
    admin_page.get_by_label("Current password",exact=True).fill("ADMIN")
    admin_page.get_by_role("button",name="Save recovery phone",exact=True).click()
    assert admin_page.get_by_label("Recovery phone number",exact=True).input_value() == "+233241234567"
    admin_page.screenshot(path=str(out / "account-settings.png"),full_page=True)
    admin_page.get_by_role("link",name="Change password",exact=True).click()
    admin_page.locator("#id_old_password").fill("ADMIN")
    admin_page.locator("#id_new_password1").fill("New-private-admin-passphrase-986!")
    admin_page.locator("#id_new_password2").fill("New-private-admin-passphrase-986!")
    admin_page.get_by_role("button",name="Change password",exact=True).click()
    admin_page.wait_for_url("http://127.0.0.1:8000/account/")
    admin_page.goto("http://127.0.0.1:8000/admin/")
    admin_page.get_by_role("heading",name="Access and configuration",exact=True).wait_for()
    admin_page.goto("http://127.0.0.1:8000/communications/")
    admin_page.screenshot(path=str(out / "communications-desktop.png"),full_page=True)
    page.set_viewport_size({"width":1440,"height":1000})
    page.goto("http://127.0.0.1:8000/stock-counts/")
    page.get_by_role("button",name="Start blind count",exact=True).click()
    count_url = page.url
    assert page.get_by_role("columnheader",name="System at start").count() == 0
    for field in page.locator('input[name^="quantity_"]').all():
        field.fill("100")
    for field in page.locator('input[name^="reason_"]').all():
        field.fill("Physical shelf verified")
    page.get_by_role("button",name="Save progress",exact=True).click()
    page.screenshot(path=str(out / "count-desktop.png"),full_page=True)
    page.set_viewport_size({"width":390,"height":844})
    page.screenshot(path=str(out / "count-mobile.png"),full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Count sheet overflows"
    page.get_by_role("button",name="Submit count",exact=True).click()
    page.get_by_role("columnheader",name="System at start",exact=True).wait_for()
    admin_page.goto(count_url)
    admin_page.get_by_label("Review note",exact=True).fill("Independent physical recount verified")
    admin_page.get_by_role("button",name="Approve and post variances",exact=True).click()
    assert admin_page.locator(".pill").filter(has_text="approved").count() == 1
    admin_page.screenshot(path=str(out / "count-approved.png"),full_page=True)
    page.set_viewport_size({"width":1440,"height":1000})
    page.goto("http://127.0.0.1:8000/operations/")
    page.locator("summary").click()
    page.get_by_label("Operation",exact=True).select_option("transfer")
    page.get_by_label("Base-unit quantity",exact=True).fill("12")
    page.get_by_label("Transfer destination",exact=True).select_option(str(warehouse.pk))
    page.get_by_label("Reason",exact=True).fill("Browser transfer discrepancy check")
    page.get_by_role("button",name="Submit for approval",exact=True).click()
    admin_page.goto("http://127.0.0.1:8000/operations/")
    transfer_row = admin_page.locator("tbody tr").filter(has_text="Browser transfer discrepancy check").first
    transfer_row.get_by_role("button",name="Approve",exact=True).click()
    page.reload()
    transfer_row = page.locator("tbody tr").filter(has_text="Browser transfer discrepancy check").first
    transfer_row.get_by_role("button",name="Dispatch",exact=True).click()
    page.get_by_label("Location",exact=True).select_option(str(warehouse.pk))
    page.get_by_role("button",name="Switch",exact=True).click()
    page.goto("http://127.0.0.1:8000/operations/")
    transfer_row = page.locator("tbody tr").filter(has_text="Browser transfer discrepancy check").first
    transfer_row.get_by_label("Sellable units received",exact=True).fill("9")
    transfer_row.get_by_label("Receipt / discrepancy note",exact=True).fill("Three units missing at delivery")
    transfer_row.get_by_role("button",name="Record receipt",exact=True).click()
    transfer_row = page.locator("tbody tr").filter(has_text="Browser transfer discrepancy check").first
    assert transfer_row.locator(".pill").inner_text() == "discrepancy"
    admin_page.get_by_label("Location",exact=True).select_option(str(warehouse.pk))
    admin_page.get_by_role("button",name="Switch",exact=True).click()
    admin_page.goto("http://127.0.0.1:8000/operations/")
    transfer_row = admin_page.locator("tbody tr").filter(has_text="Browser transfer discrepancy check").first
    transfer_row.get_by_label("Resolution note",exact=True).fill("Missing three units subsequently arrived intact")
    transfer_row.get_by_role("button",name="Confirm remainder arrived",exact=True).click()
    transfer_row = admin_page.locator("tbody tr").filter(has_text="Browser transfer discrepancy check").first
    assert transfer_row.locator(".pill").inner_text() == "received"
    admin_page.screenshot(path=str(out / "transfer-resolved.png"),full_page=True)
    admin_page.set_viewport_size({"width":390,"height":844})
    admin_page.screenshot(path=str(out / "operations-mobile.png"),full_page=True)
    assert admin_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Operations overflows"
    admin_page.goto("http://127.0.0.1:8000/reports/?family=branches")
    admin_page.get_by_role("heading",name="Branch comparison",exact=True).wait_for()
    assert admin_page.locator("tbody tr").count() == 2
    admin_page.screenshot(path=str(out / "branch-comparison-mobile.png"),full_page=True)
    assert admin_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Branch report overflows"
    admin_page.set_viewport_size({"width":1440,"height":1000})
    admin_page.screenshot(path=str(out / "branch-comparison-desktop.png"),full_page=True)
    assert not errors, errors
    browser.close()
print("Direct admin login, optional password change, recovery phone, cart-preserving search, lost-response checkout recovery, physical counts, transfer discrepancies, and desktop/mobile checks passed.")
