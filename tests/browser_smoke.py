"""Remote browser evidence: real login, navigation, checkout and narrow-screen overflow."""
import os
import time
import base64
import hashlib
import hmac
import struct
from pathlib import Path

from playwright.sync_api import sync_playwright

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
    page.get_by_label("Username", exact=True).fill("demo")
    page.get_by_label("Password", exact=True).fill("isolated-demo-browser-password")
    page.get_by_role("button", name="Sign in", exact=False).click()
    page.wait_for_url("http://127.0.0.1:8000/mfa/")
    secret = page.locator(".secret").inner_text().strip()
    step = int(time.time() // 30)
    digest = hmac.new(base64.b32decode(secret),struct.pack(">Q",step),hashlib.sha1).digest()
    offset = digest[-1] & 15
    code = (struct.unpack(">I",digest[offset:offset+4])[0] & 0x7fffffff) % 1000000
    page.get_by_label("Six-digit authenticator code").fill(f"{code:06}")
    page.get_by_role("button",name="Verify",exact=True).click()
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
    admin_page = browser.new_page(viewport={"width":1280,"height":900})
    admin_page.goto("http://127.0.0.1:8000/login/")
    admin_page.get_by_label("Username",exact=True).fill("ADMIN")
    admin_page.get_by_label("Password",exact=True).fill("ADMIN")
    admin_page.get_by_role("button",name="Sign in",exact=False).click()
    admin_page.wait_for_url("**/account/password/")
    admin_page.screenshot(path=str(out / "admin-first-login.png"),full_page=True)
    admin_page.locator("#id_old_password").fill("ADMIN")
    admin_page.locator("#id_new_password1").fill("New-private-admin-passphrase-986!")
    admin_page.locator("#id_new_password2").fill("New-private-admin-passphrase-986!")
    admin_page.get_by_role("button",name="Set password",exact=True).click()
    admin_page.wait_for_url("**/mfa/")
    secret = admin_page.locator(".secret").inner_text().strip()
    step = int(time.time() // 30)
    digest = hmac.new(base64.b32decode(secret),struct.pack(">Q",step),hashlib.sha1).digest()
    offset = digest[-1] & 15
    code = (struct.unpack(">I",digest[offset:offset+4])[0] & 0x7fffffff) % 1000000
    admin_page.get_by_label("Six-digit authenticator code").fill(f"{code:06}")
    admin_page.get_by_role("button",name="Verify",exact=True).click()
    admin_page.wait_for_url("http://127.0.0.1:8000/")
    admin_page.goto("http://127.0.0.1:8000/admin/")
    admin_page.get_by_role("heading",name="Access and configuration",exact=True).wait_for()
    admin_page.goto("http://127.0.0.1:8000/communications/")
    admin_page.screenshot(path=str(out / "communications-desktop.png"),full_page=True)
    assert not errors, errors
    browser.close()
print("Owner MFA, cart-preserving search, lost-response checkout recovery, and desktop/mobile checks passed.")
