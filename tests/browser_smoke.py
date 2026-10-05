"""Remote browser evidence: real login, navigation, checkout and narrow-screen overflow."""
import base64
import os
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

# Isolated CI fixtures only; never run this script against production.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sessions.backends.db import SessionStore
from django.utils import timezone
from core import services as core_services
from core.models import Branch, Document, Party, Product, Stock
from marketplace import services as market_services
from marketplace.models import Conversation, CustomerAccount, MarketListing, MarketPaymentAttempt
if not settings.DEBUG:
    raise RuntimeError("Browser fixtures are forbidden outside DEBUG environments.")
warehouse, _ = Branch.objects.get_or_create(code="browser-wh", defaults={"name": "Browser warehouse"})
demo_user = User.objects.get(username="demo")
demo_user.access.branches.add(warehouse)
main_branch = Branch.objects.get(code="main")
demo_customer = Party.objects.get(branch=main_branch, kind="customer", name="Sample Trading Store")
demo_product = Product.objects.get(sku="KFD-001")
browser_debt = core_services.post_trade(
    demo_user,
    main_branch,
    {
        "party": demo_customer.pk,
        "items": [{"product": demo_product.pk, "mode": "retail_unit", "quantity": 1}],
        "payments": [],
        "due_date": (timezone.localdate() + timedelta(days=7)).isoformat(),
    },
    uuid.uuid5(uuid.NAMESPACE_URL, "kofad-browser-debt-fixture"),
)

browser_market_customer, _ = CustomerAccount.objects.get_or_create(
    phone="+233245550090",
    defaults={
        "full_name": "Mobile Market Customer",
        "email": "mobile-market@example.test",
        "verified_at": timezone.now(),
        "password_hash": "!",
    },
)
browser_market_customer.full_name = "Mobile Market Customer"
browser_market_customer.email = "mobile-market@example.test"
browser_market_customer.verified_at = browser_market_customer.verified_at or timezone.now()
browser_market_customer.active = True
browser_market_customer.save(update_fields=["full_name", "email", "verified_at", "active"])
browser_listing, _ = MarketListing.objects.get_or_create(
    product=demo_product,
    defaults={"enabled": True, "title": demo_product.name, "price_source": "retail_unit"},
)
if not browser_listing.enabled:
    browser_listing.enabled = True
    browser_listing.save(update_fields=["enabled"])
browser_stock, _ = Stock.objects.get_or_create(
    branch=main_branch, product=demo_product, defaults={"quantity": 50},
)
if browser_stock.quantity < 10:
    browser_stock.quantity = 50
    browser_stock.save(update_fields=["quantity"])
browser_market_order = market_services.create_order(
    browser_market_customer,
    {str(browser_listing.pk): 1},
    {
        "fulfilment": "pickup",
        "recipient_name": browser_market_customer.full_name,
        "phone": browser_market_customer.phone,
        "email": browser_market_customer.email,
        "delivery_zone": None,
        "region": "",
        "town": "",
        "address_line": "",
        "landmark": "",
        "ghana_post_gps": "",
        "latitude": None,
        "longitude": None,
        "customer_note": "",
    },
)
browser_payment_reference = "BROWSER-" + uuid.uuid4().hex[:12].upper()
MarketPaymentAttempt.objects.create(
    order=browser_market_order,
    reference=browser_payment_reference,
    amount=browser_market_order.total,
    currency="GHS",
    status="pending",
)
browser_market_order = market_services.finalize_payment(
    browser_payment_reference,
    {
        "status": "success",
        "amount": int(browser_market_order.total * 100),
        "currency": "GHS",
        "channel": "mobile_money",
    },
)
browser_customer_session = SessionStore()
browser_customer_session["market_customer_id"] = browser_market_customer.pk
browser_customer_session["market_cart"] = {str(browser_listing.pk): 1}
browser_customer_session.create()

browser_support = Conversation.objects.create(
    customer=browser_market_customer,
    public_name=browser_market_customer.full_name,
    public_phone=browser_market_customer.phone,
    subject="Browser live support",
)
browser_support.messages.create(
    sender_type="customer",
    body="I need help with my order.",
    read_by_customer=True,
)

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
    assert float(page.locator(".premium-login-form label").first.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")) >= 13
    assert float(page.locator(".premium-login-form input").first.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")) >= 14
    assert page.get_by_text("Private setup key", exact=True).count() == 0
    icon_hrefs = [page.locator('link[rel="icon"]').nth(i).get_attribute("href") or "" for i in range(page.locator('link[rel="icon"]').count())]
    assert any(href.endswith("/static/brand/kofad-emblem.png") for href in icon_hrefs)
    assert page.locator('img[src$="/static/brand/kofad-emblem.png"]').count() >= 1
    assert page.get_by_text("KOPEX", exact=True).count() >= 1
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
    page.get_by_label("Username or phone number", exact=True).fill("demo")
    page.get_by_label("Password", exact=True).fill("isolated-demo-browser-password")
    page.get_by_role("button", name="Sign in", exact=False).click()
    page.wait_for_url("http://127.0.0.1:8000/workspace/")
    page.get_by_role("heading",name="Command centre",exact=True).wait_for()
    page.screenshot(path=str(out / "dashboard-desktop.png"), full_page=True)
    page.goto("http://127.0.0.1:8000/sales/new/")
    assert page.locator(".search-result-card").count() == 0
    assert page.get_by_text("Find a product", exact=True).is_visible()
    counter_panel = page.locator("#checkout-panel")
    assert page.locator("#sale-payment-dialog").count() == 0
    assert page.locator("#open-payment").count() == 0
    assert page.evaluate("getComputedStyle(document.body).overflow !== 'hidden'")
    assert counter_panel.evaluate("el => !['auto','scroll','hidden'].includes(getComputedStyle(el).overflow)")
    page.locator("#product-query").fill("Classic leather")
    page.locator("#catalog-search").get_by_role("button",name="Search",exact=True).click()
    first_product = page.locator(".search-result-card").filter(has_text="Classic leather sandals")
    first_product.wait_for()
    first_product.locator(".product-composer input[type='number']").first.fill("1")
    first_product.get_by_role("button",name="Add to sale",exact=False).click()
    assert page.locator("#cart-count").inner_text() == "1 lines"
    assert page.locator("#product-query").input_value() == ""
    assert page.locator(".search-result-card").count() == 0
    page.locator("#product-query").fill("Everyday cotton")
    page.locator("#catalog-search").get_by_role("button",name="Search",exact=True).click()
    second_product = page.locator(".search-result-card").filter(has_text="Everyday cotton tee")
    second_product.wait_for()
    second_product.locator(".product-composer input[type='number']").first.fill("1")
    second_product.get_by_role("button",name="Add to sale",exact=False).click()
    assert page.locator("#cart-count").inner_text() == "2 lines"
    page.locator("#customer-search").fill("Sample Trading")
    page.locator(".customer-result").filter(has_text="Sample Trading Store").click()
    assert page.locator("#customer-consent").is_checked(), "Receipt SMS should default on for an attached customer"
    page.locator("#checkout-panel").scroll_into_view_if_needed()
    assert page.locator("#single-payment-value").is_visible()
    assert page.locator("#single-payment-value").input_value() == page.locator("#total").inner_text()
    assert page.locator("#pay-cash").input_value() == page.locator("#total").inner_text()
    assert page.get_by_role("button", name="Complete Sale & Generate Receipt", exact=True).is_visible()
    page.screenshot(path=str(out / "pos-payment-desktop.png"), full_page=True)

    lost_response = []
    def lose_confirmed_response(route):
        response = route.fetch()
        assert response.ok
        lost_response.append(response.json())
        route.abort()
    page.route("**/api/trades/",lose_confirmed_response)
    page.get_by_role("button",name="Complete Sale & Generate Receipt",exact=True).click()
    page.locator("#pos-error").wait_for(state="visible")
    assert page.locator("#party").is_disabled()
    page.unroute("**/api/trades/",lose_confirmed_response)
    page.once("dialog",lambda dialog: dialog.accept())
    page.reload()
    assert page.locator("#cart-count").inner_text() == "2 lines"
    page.get_by_role("button",name="Complete Sale & Generate Receipt",exact=True).click()

    success_dialog = page.locator("#sale-success-dialog")
    success_dialog.wait_for(state="visible")
    assert page.url.endswith("/sales/new/")
    assert success_dialog.get_by_text(lost_response[0]["reference"], exact=True).is_visible()
    document_id = lost_response[0]["document_id"]
    assert page.locator("#receipt-print").get_attribute("href") == f"/documents/{document_id}/pdf/thermal80/"
    assert page.locator("#receipt-a4").get_attribute("href") == f"/documents/{document_id}/pdf/a4/"
    assert page.locator("#receipt-view").get_attribute("href") == lost_response[0]["url"]
    assert page.locator("#receipt-sms").is_visible()
    page.screenshot(path=str(out / "receipt-actions-desktop.png"), full_page=True)
    page.get_by_role("button",name="Start next sale",exact=True).click()
    assert page.locator("#cart-count").inner_text() == "0 lines"
    assert page.locator("#product-query").evaluate("el => el === document.activeElement")

    page.goto("http://127.0.0.1:8000/debts/?customer=" + str(demo_customer.pk))
    page.get_by_role("button", name="Record partial payment", exact=True).click()
    debt_dialog = page.locator("#debt-payment-box")
    debt_dialog.wait_for(state="visible")
    box = debt_dialog.bounding_box()
    assert box["y"] >= 0 and box["y"] + box["height"] <= page.viewport_size["height"] + 2
    assert page.locator("body").evaluate("el => getComputedStyle(el).overflow") == "hidden"
    page.screenshot(path=str(out / "debt-payment-dialog-desktop.png"), full_page=True)
    debt_dialog.get_by_role("button", name="Cancel", exact=True).click()

    page.goto("http://127.0.0.1:8000/products/new/")
    page.locator("#id_pack_enabled").select_option("no")
    assert page.get_by_text("Single-unit product", exact=True).is_visible()
    assert not page.locator("[data-pack-only]").first.is_visible()
    assert page.locator("#retail-unit-label").inner_text() == "Retail price"
    page.screenshot(path=str(out / "product-single-unit-desktop.png"), full_page=True)

    page.goto("http://127.0.0.1:8000/sales/new/")
    page.screenshot(path=str(out / "pos-desktop.png"), full_page=True)
    for path in ["/workspace/","/inventory/","/finance/","/operations/","/reports/","/communications/"]:
        page.goto("http://127.0.0.1:8000"+path)
        assert page.locator("h1").count() > 0
    page.set_viewport_size({"width":390,"height":844})
    page.goto("http://127.0.0.1:8000/debts/?customer=" + str(demo_customer.pk))
    page.get_by_role("button", name="Record partial payment", exact=True).click()
    mobile_dialog = page.locator("#debt-payment-box")
    mobile_dialog.wait_for(state="visible")
    mobile_box = mobile_dialog.bounding_box()
    assert abs((mobile_box["y"] + mobile_box["height"]) - 844) < 5
    assert mobile_box["height"] <= 844 * .9
    page.screenshot(path=str(out / "debt-payment-sheet-mobile.png"), full_page=True)
    mobile_dialog.get_by_role("button", name="Cancel", exact=True).click()

    for name,path in [("dashboard","/workspace/"),("pos","/sales/new/"),("inventory","/inventory/")]:
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
    assert page.locator(".search-result-card").count() == 0
    page.locator("#product-query").fill("Classic leather")
    page.locator("#catalog-search").get_by_role("button",name="Search",exact=True).click()
    mobile_product = page.locator(".search-result-card").filter(has_text="Classic leather sandals")
    mobile_product.wait_for()
    mobile_product.locator(".product-composer input[type='number']").first.fill("1")
    mobile_product.get_by_role("button",name="Add to sale",exact=False).click()
    assert page.locator("#mobile-cart-count").inner_text() == "1"
    page.locator("#cart-jump").click()
    assert page.locator("#checkout-panel").evaluate("el => el === document.activeElement")
    assert page.locator("#sale-payment-dialog").count() == 0
    assert page.locator("#single-payment-value").is_visible()
    assert page.get_by_role("button",name="Complete Sale & Generate Receipt",exact=True).is_visible()
    page.screenshot(path=str(out / "pos-payment-mobile.png"), full_page=True)
    page.locator("#cart").get_by_role("button",name="Remove",exact=False).click()
    for width in (320, 768):
        page.set_viewport_size({"width":width,"height":900})
        for path in ("/workspace/", "/sales/new/", "/inventory/", "/finance/"):
            page.goto("http://127.0.0.1:8000"+path)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), str(width)+path
    page.set_viewport_size({"width":390,"height":844})
    admin_page = browser.new_page(viewport={"width":1280,"height":900})
    admin_page.goto("http://127.0.0.1:8000/login/")
    admin_page.get_by_label("Username or phone number",exact=True).fill("admin")
    admin_page.get_by_label("Password",exact=True).fill("admin")
    admin_page.get_by_role("button",name="Sign in",exact=False).click()
    admin_page.wait_for_url("http://127.0.0.1:8000/workspace/")
    admin_page.get_by_role("heading",name="Command centre",exact=True).wait_for()
    admin_page.screenshot(path=str(out / "admin-direct-login.png"),full_page=True)
    assert float(admin_page.locator(".sidebar nav a").first.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")) >= 13

    admin_page.set_viewport_size({"width":390,"height":844})
    approval = admin_page.locator("#approval-attention")
    approval.evaluate("el => { el.hidden = false; }")
    handle = approval.locator(".approval-drag-handle")
    handle.wait_for(state="visible")
    before = approval.bounding_box()
    handle_box = handle.bounding_box()
    admin_page.mouse.move(
        handle_box["x"] + handle_box["width"] / 2,
        handle_box["y"] + handle_box["height"] / 2,
    )
    admin_page.mouse.down()
    admin_page.mouse.move(handle_box["x"] - 120, handle_box["y"] - 90, steps=8)
    admin_page.mouse.up()
    after = approval.bounding_box()
    assert abs(after["x"] - before["x"]) > 40 or abs(after["y"] - before["y"]) > 40
    assert admin_page.evaluate("Boolean(localStorage.getItem(\'kofad-approval-position-v1\'))")
    assert approval.evaluate("el => getComputedStyle(el).animationName") == "none"

    admin_page.goto(f"http://127.0.0.1:8000/online-inbox/{browser_support.pk}/?status=waiting")
    assert "support-workspace-page" in (admin_page.get_attribute("body", "class") or "")
    assert admin_page.get_by_role("button", name="Accept chat", exact=True).count() >= 1
    assert admin_page.locator(".support-waiting-callout").is_visible()
    assert float(admin_page.locator(".support-queue-copy strong").first.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")) >= 11
    desk = admin_page.locator(".support-desk-v3")
    desk_box = desk.bounding_box()
    assert desk_box["y"] >= 0 and desk_box["y"] + desk_box["height"] <= 1000 + 2
    admin_page.screenshot(path=str(out / "customer-inbox-desktop.png"), full_page=True)
    admin_page.get_by_role("button", name="Accept chat", exact=True).first.click()
    admin_page.wait_for_load_state("networkidle")
    assert admin_page.get_by_text("You are connected", exact=False).count() >= 1
    admin_page.set_viewport_size({"width":390,"height":844})
    admin_page.screenshot(path=str(out / "customer-inbox-mobile.png"), full_page=True)
    assert admin_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Customer inbox overflows"
    mobile_desk = admin_page.locator(".support-desk-v3").bounding_box()
    assert mobile_desk["y"] >= 0 and mobile_desk["y"] + mobile_desk["height"] <= 844 + 2
    admin_page.set_viewport_size({"width":1280,"height":900})

    admin_page.goto("http://127.0.0.1:8000/market-catalog/")
    admin_page.locator(".market-admin-product").first.wait_for()
    assert float(admin_page.locator(".market-admin-copy h3").first.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")) >= 14
    assert float(admin_page.locator(".market-admin-health span").first.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")) >= 9
    assert admin_page.locator(".market-admin-list").evaluate("el => getComputedStyle(el).gridTemplateColumns.split(\' \').length") >= 2
    admin_page.screenshot(path=str(out / "market-catalog-readable.png"), full_page=True)

    admin_page.goto("http://127.0.0.1:8000/documents/?kind=sale")
    admin_page.locator("tbody td").first.wait_for()
    assert float(admin_page.locator("tbody td").first.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")) >= 13
    assert admin_page.locator("tbody td").first.evaluate("el => getComputedStyle(el).color") != "rgb(255, 255, 255)"
    admin_page.evaluate("localStorage.setItem(\'kofad-theme\', \'dark\')")
    admin_page.reload()
    dark_input = admin_page.locator("input:visible").first
    dark_input.wait_for()
    assert dark_input.evaluate("el => getComputedStyle(el).backgroundColor") != "rgb(255, 255, 255)"
    assert dark_input.evaluate("el => getComputedStyle(el).color") != "rgb(0, 0, 0)"
    admin_page.screenshot(path=str(out / "sales-history-dark-readable.png"), full_page=True)
    admin_page.evaluate("localStorage.setItem(\'kofad-theme\', \'light\')")
    admin_page.reload()

    admin_page.goto("http://127.0.0.1:8000/administration/")
    admin_sidebar = admin_page.locator(".sidebar")
    max_sidebar_scroll = admin_sidebar.evaluate("el => { el.scrollTop = el.scrollHeight; return el.scrollTop; }")
    assert max_sidebar_scroll > 50
    admin_page.get_by_role("link",name="Workers",exact=True).click()
    admin_page.wait_for_url("http://127.0.0.1:8000/workers/")
    admin_page.wait_for_timeout(100)
    assert admin_page.locator(".sidebar").evaluate("el => el.scrollTop") > 50
    admin_page.goto("http://127.0.0.1:8000/account/")
    admin_page.get_by_label("Recovery phone number",exact=False).fill("0241234567")
    admin_page.get_by_label("Current password",exact=False).fill("admin")
    admin_page.get_by_role("button",name="Save recovery phone",exact=True).click()
    assert admin_page.get_by_label("Recovery phone number",exact=False).input_value() == "+233241234567"
    admin_page.screenshot(path=str(out / "account-settings.png"),full_page=True)
    admin_page.get_by_role("link",name="Change password",exact=True).click()
    admin_page.locator("#id_old_password").fill("admin")
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
    market_page = browser.new_page(viewport={"width":390,"height":844})
    market_page.context.add_cookies([{
        "name": settings.SESSION_COOKIE_NAME,
        "value": browser_customer_session.session_key,
        "url": "http://127.0.0.1:8000",
    }])
    osm_tile_requests = []
    transparent_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )

    def serve_osm_tile(route):
        osm_tile_requests.append(route.request)
        route.fulfill(status=200, content_type="image/png", body=transparent_png)

    market_page.route("https://tile.openstreetmap.org/**", serve_osm_tile)
    market_page.route(
        "https://images.unsplash.com/**",
        lambda route: route.fulfill(status=200, content_type="image/png", body=transparent_png),
    )

    market_page.goto("http://127.0.0.1:8000/")
    assert market_page.locator(".commerce-global-search").count() == 0
    assert market_page.locator(".commerce-category-section").count() == 0
    assert market_page.get_by_text("SHOP BY DEPARTMENT", exact=False).count() == 0
    assert market_page.locator(".home-hero-v9").count() == 1
    assert market_page.locator(".home-hero-products").count() == 0
    assert market_page.get_by_text("Retail or wholesale.", exact=False).count() >= 1
    assert market_page.get_by_text("Wholesale quantities", exact=True).count() == 1
    assert market_page.locator(".home-featured-grid .market-product-card").count() <= 3
    assert market_page.locator(".public-mobile-actions").is_visible()
    assert market_page.locator(".market-cart-link").count() == 0
    mobile_hero_background = market_page.locator(".home-hero-v9").evaluate(
        "el => getComputedStyle(el).backgroundImage"
    )
    assert "images.unsplash.com/photo-1768176136613-96a7bfc0049e" in mobile_hero_background
    assert "w=1800" in mobile_hero_background
    assert "kofad-market-retail-hero" in mobile_hero_background
    assert "kofad-market-hero.svg" not in mobile_hero_background
    hero_asset = market_page.request.get(
        "http://127.0.0.1:8000/static/marketplace/kofad-market-retail-hero.webp"
    )
    assert hero_asset.ok, "Bundled homepage hero image is not being served"
    assert hero_asset.headers.get("content-type", "").startswith("image/webp")
    market_page.screenshot(path=str(out / "homepage-market-mobile.png"), full_page=True)

    market_page.set_viewport_size({"width":1440,"height":1000})
    market_page.goto("http://127.0.0.1:8000/")
    desktop_hero_background = market_page.locator(".home-hero-v9").evaluate(
        "el => getComputedStyle(el).backgroundImage"
    )
    assert "images.unsplash.com/photo-1768176136613-96a7bfc0049e" in desktop_hero_background
    assert "w=3000" in desktop_hero_background
    assert "kofad-market-retail-hero" in desktop_hero_background
    assert "kofad-market-hero.svg" not in desktop_hero_background
    market_page.screenshot(path=str(out / "homepage-market-desktop.png"), full_page=True)
    market_page.set_viewport_size({"width":390,"height":844})
    market_page.goto("http://127.0.0.1:8000/")

    # Enter Market from the public site, then move within Market. Back inside the
    # same authenticated zone must navigate normally without a logout prompt.
    market_page.locator(".public-mobile-market").click()
    market_page.wait_for_url("http://127.0.0.1:8000/market/")
    market_page.set_viewport_size({"width":1440,"height":1000})
    first_market_title = market_page.locator(".shop-product-grid .commerce-card-body h3").first
    first_market_title.wait_for()
    assert float(first_market_title.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")) >= 14
    market_page.evaluate("localStorage.setItem(\'kofad-theme\', \'dark\')")
    market_page.reload()
    sort_select = market_page.locator(".shop-sort select")
    assert sort_select.evaluate("el => getComputedStyle(el).backgroundColor") != "rgb(255, 255, 255)"
    assert sort_select.evaluate("el => getComputedStyle(el).color") != "rgb(0, 0, 0)"
    market_page.screenshot(path=str(out / "market-dark-readable.png"), full_page=True)
    market_page.evaluate("localStorage.setItem(\'kofad-theme\', \'light\')")
    market_page.reload()
    market_page.locator(".shop-shell-account").click()
    market_page.wait_for_url("http://127.0.0.1:8000/market/account/")
    market_page.locator(".shop-shell-orders").click()
    market_page.wait_for_url("http://127.0.0.1:8000/market/orders/")
    market_page.go_back()
    market_page.wait_for_url("http://127.0.0.1:8000/market/account/")
    assert market_page.locator("[data-session-leave-dialog]:visible").count() == 0

    # Continue back to the Market landing page without a prompt; only the next
    # Back, which would actually leave Market for the public homepage, prompts.
    market_page.go_back()
    market_page.wait_for_url("http://127.0.0.1:8000/market/")
    assert market_page.locator("[data-session-leave-dialog]:visible").count() == 0
    market_page.go_back(wait_until="domcontentloaded")
    market_page.locator("[data-session-leave-dialog]").wait_for(state="visible")
    assert market_page.get_by_role("heading", name="Leave and sign out?", exact=True).is_visible()
    market_page.get_by_role("button", name="Stay signed in", exact=True).click()
    assert "/market/" in market_page.url

    market_page.set_viewport_size({"width":390,"height":844})
    market_page.goto("http://127.0.0.1:8000/market/account/")
    assert market_page.get_by_role("heading", name="Hi, Mobile Market Customer.", exact=True).is_visible()
    assert market_page.locator(".market-mobile-dock").is_visible()
    assert market_page.get_by_text(browser_market_order.confirmed_reference, exact=True).count() >= 1
    assert market_page.get_by_text("Paystack", exact=False).count() == 0
    market_page.screenshot(path=str(out / "market-account-mobile.png"), full_page=True)
    for width in (320, 390, 768):
        market_page.set_viewport_size({"width":width,"height":900})
        for market_path in (
            "/market/",
            "/market/account/",
            "/market/orders/",
            f"/market/orders/{browser_market_order.pk}/",
            "/market/cart/",
            "/market/checkout/",
            "/market/account/wishlist/",
            "/market/messages/",
        ):
            market_page.goto("http://127.0.0.1:8000" + market_path)
            assert market_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), str(width) + market_path
    market_page.set_viewport_size({"width":390,"height":844})
    market_page.goto("http://127.0.0.1:8000/market/checkout/")
    assert market_page.locator("[data-location-map]").count() == 1
    market_page.wait_for_timeout(250)
    assert osm_tile_requests, "Checkout map did not request canonical OpenStreetMap tiles"
    assert all(
        request.url.startswith("https://tile.openstreetmap.org/")
        for request in osm_tile_requests
    )
    assert all(
        request.headers.get("referer") == "http://127.0.0.1:8000/"
        for request in osm_tile_requests
    ), "OSM tile requests must include an origin Referer"
    market_page.screenshot(path=str(out / "market-checkout-map-mobile.png"), full_page=True)
    fulfilment_select = market_page.locator("select[name='fulfilment']")
    assert fulfilment_select.input_value() == "delivery"
    assert market_page.locator("[data-delivery-fields]").get_attribute("hidden") is None
    assert market_page.locator("[data-capture-location]").is_visible()
    market_page.goto(f"http://127.0.0.1:8000/market/orders/{browser_market_order.pk}/")
    assert market_page.get_by_text(browser_market_order.confirmed_reference, exact=False).count() >= 1
    assert market_page.get_by_text(browser_payment_reference, exact=False).count() == 0
    assert market_page.get_by_text("Paystack", exact=False).count() == 0
    assert market_page.locator(".shop-shell-search").count() == 1
    market_page.screenshot(path=str(out / "market-order-mobile.png"), full_page=True)

    market_page.goto("http://127.0.0.1:8000/market/access/")
    assert market_page.locator("body.customer-session").count() == 0
    assert market_page.locator("body.market-access-page").count() == 1
    assert market_page.locator(".market-auth-header").count() == 1
    assert market_page.locator(".market-auth-brand img").count() == 1
    assert market_page.locator(".premium-access-logo img").count() == 1
    assert market_page.locator(".access-card-mark").count() == 0
    assert market_page.locator(".shop-shell-search").count() == 0
    assert market_page.locator(".shop-shell-cart").count() == 0
    assert market_page.locator(".market-flash").count() == 0
    assert market_page.get_by_text("Sign in with your verified phone number to continue.", exact=False).count() == 0
    phone_input = market_page.get_by_label("Mobile number", exact=True)
    assert phone_input.get_attribute("inputmode") == "tel"
    assert phone_input.get_attribute("autocomplete") == "tel"

    market_page.evaluate("localStorage.setItem('kofad-theme','light'); document.documentElement.dataset.theme='light'")
    market_page.screenshot(path=str(out / "market-access-light-mobile.png"), full_page=True)
    market_page.locator(".market-auth-theme").click()
    market_page.wait_for_timeout(80)
    assert market_page.evaluate("document.documentElement.dataset.theme") == "dark"
    market_page.screenshot(path=str(out / "market-access-dark-mobile.png"), full_page=True)

    market_page.set_viewport_size({"width":1440,"height":1000})
    market_page.goto("http://127.0.0.1:8000/market/access/")
    assert market_page.locator(".market-auth-header").is_visible()
    assert market_page.locator(".premium-access-card").is_visible()
    assert market_page.locator(".shop-shell-search").count() == 0
    assert market_page.locator(".shop-shell-cart").count() == 0
    market_page.screenshot(path=str(out / "market-access-desktop.png"), full_page=True)

    market_state = market_page.request.get("http://127.0.0.1:8000/market/session/state/").json()
    assert market_state["authenticated"] is False
    market_page.goto("http://127.0.0.1:8000/market/")
    assert market_page.locator(".shop-shell-search").count() == 0
    market_page.close()

    admin_page.set_viewport_size({"width":1440,"height":1000})
    admin_page.screenshot(path=str(out / "branch-comparison-desktop.png"),full_page=True)
    admin_page.goto("http://127.0.0.1:8000/login/")
    assert admin_page.get_by_label("Username or phone number", exact=True).is_visible()
    staff_state = admin_page.request.get("http://127.0.0.1:8000/session/state/").json()
    assert staff_state["authenticated"] is False
    assert not errors, errors
    browser.close()
print("Readable light/dark design system, live-product homepage hero, movable Approval Center, redesigned Customer Inbox and Market Catalog, responsive staff/customer layouts, delivery map checkout, verified order IDs, sales checkout, debt payment, stock counts and transfers passed.")
