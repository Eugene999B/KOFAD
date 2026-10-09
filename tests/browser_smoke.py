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
staff_login_url = f"http://127.0.0.1:8000/{settings.STAFF_LOGIN_SLUG}/"
staff_admin_url = f"http://127.0.0.1:8000/{settings.STAFF_LOGIN_SLUG}/technical-admin/"
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
    branch=main_branch,
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
            page.goto(staff_login_url)
            break
        except Exception:
            time.sleep(1)
    # Existing workflow evidence follows a visitor who enabled preferences.
    # Fresh essential-only and withdrawal flows are exercised by cookie_smoke.py.
    page.locator("[data-cookie-accept]").click()
    page.screenshot(path=str(out / "login-desktop.png"), full_page=True)
    assert float(page.locator(".premium-login-form label").first.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")) >= 13
    assert float(page.locator(".premium-login-form input").first.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")) >= 14
    assert page.get_by_text("Private setup key", exact=True).count() == 0
    icon_hrefs = [page.locator('link[rel="icon"]').nth(i).get_attribute("href") or "" for i in range(page.locator('link[rel="icon"]').count())]
    assert any("favicon-96" in href and ".png" in href for href in icon_hrefs)
    assert page.locator('img[src$="/static/brand/kofad-official-logo.svg"]').count() >= 1
    assert page.locator(".brand-official-logo img").count() >= 1
    assert page.locator(".brand-wordmark").count() == 0
    assert page.get_by_text(("KO" + "PEX"), exact=True).count() == 0
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
    page.goto(staff_login_url)
    page.get_by_label("Username, mobile number or verified email", exact=True).fill("demo")
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
    receipt_sms_choice = page.locator("#customer-consent")
    assert receipt_sms_choice.is_enabled(), "Receipt SMS choice must remain user-toggleable"
    assert receipt_sms_choice.is_checked(), "Receipt SMS should start checked on every new sale"
    receipt_whatsapp_choice = page.locator("#customer-whatsapp")
    assert not receipt_whatsapp_choice.is_checked()
    receipt_whatsapp_choice.check()
    assert receipt_whatsapp_choice.is_checked()
    receipt_whatsapp_choice.uncheck()
    receipt_sms_choice.uncheck()
    assert not receipt_sms_choice.is_checked(), "Cashier must be able to turn receipt SMS off before choosing a customer"
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
    # The new POS defaults to an explicit walk-in. Saved customers require
    # an intentional choice before the customer search becomes visible.
    assert page.locator('[data-customer-mode="walkin"]').get_attribute("aria-pressed") == "true"
    page.locator('[data-customer-mode="saved"]').click()
    assert page.locator("#customer-search").is_visible()
    page.locator("#customer-search").fill("Sample Trading")
    page.locator(".customer-result").filter(has_text="Sample Trading Store").click()
    assert receipt_sms_choice.is_enabled()
    assert not receipt_sms_choice.is_checked(), "Selecting a customer must preserve the cashier's SMS choice"
    page.get_by_role("button", name="Exact", exact=True).click()
    assert not receipt_sms_choice.is_checked(), "Payment changes must not force receipt SMS back on"
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
    page.goto("http://127.0.0.1:8000/debts/")
    assert page.locator(".debt-account-pane").is_visible()
    assert not page.locator(".debt-detail-pane").is_visible()
    page.locator(".debt-account-card").first.click()
    assert page.locator(".debt-detail-pane").is_visible()
    assert not page.locator(".debt-account-pane").is_visible()
    assert page.get_by_text("Back to customer accounts", exact=False).is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Debt desk overflows"
    page.screenshot(path=str(out / "debt-account-mobile-focused.png"), full_page=True)

    page.goto("http://127.0.0.1:8000/purchasing/")
    page.get_by_role("button", name="New product", exact=True).click()
    assert page.locator("#purchase-new-builder").is_visible()
    assert page.get_by_label("Product name", exact=True).is_visible()
    assert page.get_by_label("Purchase price per selected unit", exact=False).is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Purchasing new-product builder overflows"
    page.screenshot(path=str(out / "purchasing-new-product-mobile.png"), full_page=True)

    page.goto("http://127.0.0.1:8000/finance/")
    page.get_by_label("Funding source", exact=True).select_option("prior_business_funds")
    assert page.get_by_label("Daily Closing treatment", exact=True).input_value() == "0"
    assert page.get_by_text("Accounting only for Daily Closing.", exact=True).is_visible()
    page.get_by_label("Funding source", exact=True).select_option("today_sales_receipts")
    assert page.get_by_label("Daily Closing treatment", exact=True).input_value() == "1"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Finance funding controls overflow"
    page.screenshot(path=str(out / "expense-funding-mobile.png"), full_page=True)

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
    admin_page.goto(staff_login_url)
    admin_page.locator("[data-cookie-accept]").click()
    admin_page.get_by_label("Username, mobile number or verified email",exact=True).fill("admin")
    admin_page.get_by_label("Password",exact=True).fill("admin")
    admin_page.get_by_role("button",name="Sign in",exact=False).click()
    admin_page.wait_for_url("http://127.0.0.1:8000/workspace/")
    admin_page.get_by_role("heading",name="Command centre",exact=True).wait_for()
    admin_page.screenshot(path=str(out / "admin-direct-login.png"),full_page=True)
    assert float(admin_page.locator(".sidebar nav a").first.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")) >= 13

    admin_page.set_viewport_size({"width":390,"height":844})
    approval = admin_page.locator("#approval-attention")
    approval.evaluate("el => { el.hidden = false; el.classList.add('has-approvals'); }")
    assert approval.locator(".approval-drag-handle").count() == 0
    before = approval.bounding_box()
    assert before["width"] < 100, "Approval control is too wide on mobile"
    assert before["height"] <= 50, "Approval control is too tall on mobile"
    admin_page.mouse.move(
        before["x"] + before["width"] / 2,
        before["y"] + before["height"] / 2,
    )
    admin_page.mouse.down()
    admin_page.mouse.move(before["x"] - 120, before["y"] - 90, steps=8)
    admin_page.mouse.up()
    after = approval.bounding_box()
    assert abs(after["x"] - before["x"]) > 40 or abs(after["y"] - before["y"]) > 40
    assert abs(after["width"] - before["width"]) < 1
    assert abs(after["height"] - before["height"]) < 1
    assert admin_page.evaluate("Boolean(localStorage.getItem(\'kofad-approval-position-v2\'))")
    assert approval.evaluate("el => getComputedStyle(el).animationName") != "none"

    admin_page.goto("http://127.0.0.1:8000/settings/communications/")
    assert admin_page.locator('[role="switch"]').count() == 10
    closing_switch = admin_page.locator("#id_policy-daily_closing_mode-enabled")
    closing_switch.check()
    assert closing_switch.is_checked()
    closing_switch.uncheck()
    assert not closing_switch.is_checked()
    admin_page.screenshot(path=str(out / "channel-settings-desktop.png"), full_page=True)
    admin_page.set_viewport_size({"width":390,"height":844})
    assert admin_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "channel settings overflow"
    admin_page.screenshot(path=str(out / "channel-settings-mobile.png"), full_page=True)
    admin_page.goto("http://127.0.0.1:8000/documents/?kind=sale")
    receipt_form = admin_page.locator("[data-receipt-channels]").first
    receipt_form.locator('input[name="whatsapp"]').check()
    assert receipt_form.locator('input[name="whatsapp"]').is_checked()
    assert "/documents/?" in admin_page.url, "Receipt choice must not open transaction row"
    receipt_form.locator('input[name="whatsapp"]').uncheck()
    admin_page.screenshot(path=str(out / "sales-history-mobile.png"), full_page=True)
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
    admin_page.locator(".catalog-product-card").first.wait_for()
    assert float(admin_page.locator(".catalog-product-main h3").first.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")) >= 14
    assert float(admin_page.locator(".catalog-readiness span").first.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")) >= 9
    assert admin_page.locator(".catalog-studio-list").evaluate("el => getComputedStyle(el).gridTemplateColumns.split(\' \').length") >= 2
    first_catalog_card = admin_page.locator(".catalog-product-card").first
    assert first_catalog_card.evaluate("el => getComputedStyle(el).gridTemplateColumns.split(\' \').length") >= 2
    assert first_catalog_card.locator(".catalog-product-photo").bounding_box()["x"] < first_catalog_card.locator(".catalog-product-main").bounding_box()["x"]
    assert first_catalog_card.locator(".catalog-product-commerce").evaluate("el => getComputedStyle(el).gridColumnEnd") == "-1"
    admin_page.screenshot(path=str(out / "market-catalog-readable.png"), full_page=True)

    admin_page.goto("http://127.0.0.1:8000/documents/?kind=sale")
    admin_page.locator(".sales-history-item").first.wait_for()
    sale_link = admin_page.locator(".sales-history-reference").first
    assert float(sale_link.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")) >= 13
    assert sale_link.evaluate("el => getComputedStyle(el).color") != "rgb(255, 255, 255)"
    sale_target = sale_link.get_attribute("href")
    assert sale_target and sale_target.startswith("/documents/")
    sale_link.click()
    admin_page.wait_for_url("http://127.0.0.1:8000" + sale_target)
    admin_page.goto("http://127.0.0.1:8000/documents/?kind=sale")
    admin_page.evaluate("localStorage.setItem(\'kofad-theme\', \'dark\')")
    admin_page.reload()
    dark_input = admin_page.locator("input:visible").first
    dark_input.wait_for()
    assert dark_input.evaluate("el => getComputedStyle(el).backgroundColor") != "rgb(255, 255, 255)"
    assert dark_input.evaluate("el => getComputedStyle(el).color") != "rgb(0, 0, 0)"
    admin_page.screenshot(path=str(out / "sales-history-dark-readable.png"), full_page=True)
    admin_page.evaluate("localStorage.setItem(\'kofad-theme\', \'light\')")
    admin_page.reload()

    # System-wide mobile layout hardening: representative administration,
    # finance and settings pages must fit without horizontal document overflow.
    mobile_pages = (
        "/administration/users/",
        "/administration/",
        "/workers/",
        "/parties/?kind=customer",
        "/finance/",
        "/accounting/",
        "/settings/",
        "/settings/payments/",
        "/settings/finance/",
        "/settings/receipts/",
        "/settings/debt/",
        "/settings/communications/",
    )
    for mobile_width in (390, 320):
        admin_page.set_viewport_size({"width":mobile_width,"height":844})
        for path in mobile_pages:
            admin_page.goto("http://127.0.0.1:8000" + path, wait_until="domcontentloaded")
            admin_page.wait_for_timeout(80)
            assert admin_page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            ), f"Mobile layout overflows at {mobile_width}px: {path}"

    admin_page.set_viewport_size({"width":390,"height":844})
    admin_page.goto("http://127.0.0.1:8000/administration/users/")
    staff_table = admin_page.locator(".table-wrap > table.mobile-card-table")
    staff_table.wait_for()
    first_staff_cell = staff_table.locator("tbody tr td").first
    assert first_staff_cell.get_attribute("data-mobile-label") == "Staff member"
    assert staff_table.evaluate("el => parseFloat(getComputedStyle(el).minWidth) == 0")
    admin_page.screenshot(path=str(out / "staff-users-mobile-hardened.png"), full_page=True)

    admin_page.goto("http://127.0.0.1:8000/settings/payments/")
    form_actions = admin_page.locator(".form-actions")
    form_actions.wait_for()
    assert form_actions.evaluate("el => getComputedStyle(el).position") == "static"
    assert admin_page.locator(".setting-shortcuts").evaluate(
        "el => el.getBoundingClientRect().right <= window.innerWidth + 1"
    )
    assert admin_page.locator(".form-panel").evaluate(
        "el => el.getBoundingClientRect().right <= window.innerWidth + 1"
    )
    admin_page.screenshot(path=str(out / "payment-methods-mobile-hardened.png"), full_page=True)

    admin_page.set_viewport_size({"width":1280,"height":900})
    admin_page.goto("http://127.0.0.1:8000/settings/backup/")
    reset_button = admin_page.get_by_role("button", name="Reset KOFAD to fresh start", exact=True)
    restore_button = admin_page.get_by_role("button", name="Restore full system", exact=True)
    assert reset_button.is_disabled()
    assert restore_button.is_disabled()
    admin_page.locator("#backup-current-password").fill("admin")
    admin_page.locator("#backup-passphrase").fill("CI-backup-passphrase-very-strong-42!")
    admin_page.locator("#backup-passphrase-confirm").fill("CI-backup-passphrase-very-strong-42!")
    with admin_page.expect_download(timeout=30000) as backup_download:
        admin_page.locator("[data-backup-download]").get_by_role(
            "button", name="Download encrypted KOFAD backup", exact=False
        ).click()
    download = backup_download.value
    assert download.suggested_filename.endswith(".kofad.enc")
    admin_page.wait_for_function(
        "() => !document.querySelector('[data-requires-recent-backup]').disabled",
        timeout=30000,
    )
    assert not reset_button.is_disabled()
    assert not restore_button.is_disabled()
    assert admin_page.locator("[data-backup-ready-badge]").is_visible()
    assert admin_page.locator("[data-backup-state]").get_by_text("Safety backup confirmed.", exact=True).count() == 1

    admin_page.goto("http://127.0.0.1:8000/administration/")
    admin_sidebar = admin_page.locator(".sidebar")
    max_sidebar_scroll = admin_sidebar.evaluate("el => { el.scrollTop = el.scrollHeight; return el.scrollTop; }")
    assert max_sidebar_scroll > 50
    # A late restore must not undo a scroll made immediately after navigation.
    admin_page.wait_for_timeout(150)
    assert admin_sidebar.evaluate("el => el.scrollTop") == max_sidebar_scroll
    admin_page.get_by_role("link",name="Workers",exact=True).click()
    admin_page.wait_for_url("http://127.0.0.1:8000/workers/")
    admin_page.wait_for_function("() => document.querySelector('.sidebar').scrollTop > 50", timeout=5000)
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
    admin_page.goto(staff_admin_url)
    admin_page.get_by_role("heading",name="Access and configuration",exact=True).wait_for()
    admin_page.goto("http://127.0.0.1:8000/communications/")
    admin_page.screenshot(path=str(out / "communications-desktop.png"),full_page=True)
    page.set_viewport_size({"width":1440,"height":1000})
    page.goto("http://127.0.0.1:8000/stock-counts/")
    page.get_by_role("button",name="Start blind count",exact=True).click()
    count_url = page.url
    assert page.get_by_text("System", exact=True).count() == 0
    assert page.locator('input[name^="reason_"]').count() == 0
    for field in page.locator('input[name^="quantity_"]').all():
        field.fill("100")
    page.get_by_role("button",name="Save progress",exact=True).click()
    page.screenshot(path=str(out / "count-desktop.png"),full_page=True)
    page.set_viewport_size({"width":390,"height":844})
    page.screenshot(path=str(out / "count-mobile.png"),full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Count sheet overflows"
    page.get_by_role("button",name="Submit & compare with system",exact=True).click()
    page.locator(".count-result-row").first.wait_for()
    assert page.get_by_text("Physical", exact=True).count() >= 1
    assert page.get_by_text("System", exact=True).count() >= 1
    assert page.get_by_text("Variance", exact=True).count() >= 1
    assert page.get_by_role("link", name="Excel", exact=True).is_visible()
    with page.expect_download() as count_export:
        page.get_by_role("link", name="Excel", exact=True).click()
    assert count_export.value.suggested_filename.endswith(".xlsx")
    admin_page.goto(count_url)
    admin_page.get_by_label("Review note",exact=True).fill("Independent physical recount verified")
    admin_page.get_by_role("button",name="Approve differences & update stock",exact=True).click()
    admin_page.locator(".pill").filter(has_text="Approved").first.wait_for(state="visible")
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
    market_page.route(
        "https://images.pexels.com/**",
        lambda route: route.fulfill(status=200, content_type="image/png", body=transparent_png),
    )

    market_page.goto("http://127.0.0.1:8000/")
    market_page.locator("[data-cookie-accept]").click()
    assert market_page.locator(".commerce-global-search").count() == 0
    assert market_page.locator(".commerce-category-section").count() == 0
    assert market_page.get_by_text("SHOP BY DEPARTMENT", exact=False).count() == 0
    assert market_page.locator(".kfd-hero").count() == 1
    assert market_page.locator("[data-kfd-rotator] [data-hero-image]").count() == 1
    assert market_page.get_by_text("Everything for today.", exact=False).count() >= 1
    assert market_page.locator(".kfd-category-grid .kfd-category").count() == 4
    assert all(img.evaluate("node => node.complete && node.naturalWidth > 0") for img in market_page.locator(".kfd-category-grid .kfd-category img").all())
    assert market_page.locator("[data-hero-image]").evaluate("img => img.naturalWidth > 0")
    assert market_page.locator(".home-featured-grid .market-product-card").count() <= 3
    assert market_page.locator(".public-mobile-actions").is_visible()
    assert market_page.locator(".market-cart-link").count() == 0
    assert market_page.locator(".kfd-hero").evaluate("el => el.getBoundingClientRect().height") <= 400
    assert market_page.locator("[data-hero-count]").inner_text() == "01 / 06"
    market_page.screenshot(path=str(out / "homepage-market-mobile.png"), full_page=True)

    market_page.set_viewport_size({"width":1440,"height":1000})
    market_page.goto("http://127.0.0.1:8000/")
    assert market_page.locator(".kfd-hero").evaluate("el => el.getBoundingClientRect().height") <= 365
    assert "kofad-market-retail-hero" in market_page.locator("[data-hero-image]").get_attribute("src")
    assert market_page.get_by_role("button", name="Next photograph").is_visible()
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
    market_page.locator(".shop-account-popover").wait_for(state="visible")
    market_page.locator(".shop-account-popover a[href='/market/account/']").click()
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
            if market_path == "/market/messages/":
                # Regression: support-v3 used to hide the entire new chat
                # composer on screens <= 680px, so the Start button was unusable.
                assert market_page.locator(".support-new-panel-v3 form").is_visible(), str(width)
                assert market_page.get_by_role("button", name="Start chat").is_visible(), str(width)
    market_page.set_viewport_size({"width":390,"height":844})
    market_page.goto("http://127.0.0.1:8000/market/messages/")
    assert market_page.locator(".support-new-panel-v3 form").is_visible()
    market_page.locator(".support-new-panel-v3 input[name='subject']").fill("Browser support request")
    market_page.locator(".support-new-panel-v3 textarea[name='message']").fill("I need help with my order.")
    market_page.get_by_role("button", name="Start chat").click()
    market_page.wait_for_url("**/market/messages/*/")
    assert market_page.locator(".support-message.customer").count() == 1
    assert market_page.locator("[data-live-thread]").is_visible()
    assert "I need help with my order." in market_page.locator(".support-stream-v3").inner_text()
    market_page.screenshot(path=str(out / "market-support-new-chat-mobile.png"), full_page=True)
    market_page.goto("http://127.0.0.1:8000/market/messages/")
    assert market_page.locator(".support-new-panel-v3 form").is_visible()
    market_page.set_viewport_size({"width":390,"height":844})
    market_page.goto("http://127.0.0.1:8000/market/checkout/")
    assert market_page.locator("[data-location-map]").count() == 1
    assert not market_page.locator("[data-location-map]").is_visible()
    market_page.locator("[data-map-details] summary").click()
    assert market_page.locator("[data-location-map]").is_visible()
    market_page.wait_for_timeout(250)
    assert market_page.get_by_text("Open map fallback", exact=True).count() >= 1
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
    fulfilment_select.select_option("pickup")
    assert not market_page.locator("[data-delivery-fields]").is_visible()
    assert market_page.locator("[data-pickup-fields]").is_visible()
    assert market_page.locator("#id_address_line").is_disabled()
    assert market_page.locator("[data-delivery-fee]").inner_text() == "GHS 0.00"
    assert market_page.locator("[data-checkout-total]").inner_text() == market_page.locator("[data-checkout-subtotal]").inner_text()
    assert market_page.locator(".shop-account-menu > summary").is_visible()
    market_page.locator(".shop-account-menu > summary").click()
    assert market_page.locator(".shop-account-signout button").is_visible()
    market_page.keyboard.press("Escape")
    market_page.screenshot(path=str(out / "market-pickup-mobile.png"), full_page=True)
    fulfilment_select.select_option("delivery")
    assert market_page.locator("[data-delivery-fields]").is_visible()
    assert market_page.locator("#id_address_line").is_enabled()
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
    market_page.set_viewport_size({"width":390,"height":844})
    market_page.goto("http://127.0.0.1:8000/market/")
    assert market_page.locator(".shop-product-grid").is_visible()
    assert market_page.locator(".shop-shell-search").is_visible()
    assert market_page.locator(".shop-shell-signin").is_visible()
    assert market_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    market_page.screenshot(path=str(out / "market-public-mobile.png"), full_page=True)
    market_page.set_viewport_size({"width":1440,"height":1000})
    market_page.goto("http://127.0.0.1:8000/market/")
    assert market_page.locator(".shop-product-grid").is_visible()
    market_page.screenshot(path=str(out / "market-public-desktop.png"), full_page=True)
    # Company pages remain public, readable and operable on small phones.
    for width in (320, 390, 1440):
        market_page.set_viewport_size({"width": width, "height": 900})
        for public_path in ("/about/", "/faq/", "/delivery/", "/returns-policy/", "/terms/", "/privacy/", "/contact/"):
            market_page.goto("http://127.0.0.1:8000" + public_path)
            assert market_page.locator("h1").is_visible()
            assert market_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), public_path + str(width)
        market_page.screenshot(path=str(out / ("company-contact-" + str(width) + ".png")), full_page=True)
    market_page.goto("http://127.0.0.1:8000/faq/")
    market_page.get_by_label("Search help", exact=True).fill("payment")
    assert market_page.locator("[data-help-answer]:visible").count() > 0
    assert market_page.locator("[data-help-answer]:visible").count() < market_page.locator("[data-help-answer]").count()
    market_page.get_by_label("Search help", exact=True).fill("zzznomatch")
    assert market_page.locator("[data-help-empty]").is_visible()
    market_page.get_by_role("button", name="Clear", exact=True).click()
    assert market_page.locator("[data-help-answer]:visible").count() == market_page.locator("[data-help-answer]").count()
    market_page.locator(".company-faq summary").first.click()
    assert market_page.locator(".company-faq").first.get_attribute("open") is not None
    market_page.set_viewport_size({"width": 390, "height": 844})
    market_page.goto("http://127.0.0.1:8000/returns-policy/")
    market_page.evaluate("localStorage.setItem('kofad-theme','dark'); document.documentElement.dataset.theme='dark'")
    assert market_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    market_page.screenshot(path=str(out / "company-returns-dark-mobile.png"), full_page=True)
    for width in (390, 1440):
        market_page.set_viewport_size({"width": width, "height": 900})
        for public_name in ("about", "faq"):
            market_page.goto("http://127.0.0.1:8000/" + public_name + "/")
            market_page.screenshot(path=str(out / (public_name + "-" + str(width) + ".png")), full_page=True)
    market_page.close()

    # App links must open the dialler/mail app without ending the staff session.
    admin_page.goto("http://127.0.0.1:8000/workspace/")
    for app_href in ("tel:+233245550090", "mailto:fixture@example.test"):
        cancelled = admin_page.evaluate("""href => {
            const link = document.createElement("a");
            link.href = href;
            link.textContent = "Contact fixture";
            document.body.appendChild(link);
            const event = new MouseEvent("click", {bubbles: true, cancelable: true});
            // Observe the application's decision, then suppress the OS app launch.
            let guarded = false;
            window.addEventListener("click", event => {
                guarded = event.defaultPrevented;
                event.preventDefault();
            }, {once: true});
            link.dispatchEvent(event);
            link.remove();
            return guarded;
        }""", app_href)
        assert cancelled is False
        assert admin_page.locator("[data-session-leave-dialog]:visible").count() == 0
    admin_page.reload()
    assert settings.STAFF_LOGIN_PATH not in admin_page.url
    
    # Executive and accounting workspaces remain readable across narrow phones
    # and dark theme. Market Catalog Studio must expose real customer visibility.
    for width in (320, 390):
        admin_page.set_viewport_size({"width":width,"height":900})
        for staff_path in (
            "/workspace/",
            "/accounting/?view=overview",
            "/market-catalog/",
            "/communications/",
            "/settings/online-payments/",
        ):
            admin_page.goto("http://127.0.0.1:8000" + staff_path)
            assert admin_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), str(width) + staff_path

    admin_page.set_viewport_size({"width":390,"height":844})
    admin_page.goto("http://127.0.0.1:8000/workspace/")
    assert admin_page.locator(".top-right form[action=\"/logout/\"] button").is_visible()
    assert admin_page.locator(".executive-kpis").is_visible()
    assert admin_page.get_by_text("Operational control pulse", exact=True).is_visible()
    admin_page.evaluate("localStorage.setItem('kofad-theme','dark'); document.documentElement.dataset.theme='dark'")
    admin_page.reload()
    assert admin_page.locator(".executive-kpis article").first.evaluate(
        "el => getComputedStyle(el).backgroundColor"
    ) != "rgb(255, 255, 255)"
    admin_page.screenshot(path=str(out / "executive-overview-dark-mobile.png"), full_page=True)

    admin_page.goto("http://127.0.0.1:8000/accounting/")
    assert admin_page.locator(".accounting-executive-metrics").is_visible()
    assert admin_page.get_by_text("Cash & equivalents", exact=True).count() >= 1
    assert admin_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    admin_page.screenshot(path=str(out / "accounting-dark-mobile.png"), full_page=True)

    admin_page.goto("http://127.0.0.1:8000/market-catalog/")
    assert admin_page.locator(".catalog-product-card").count() >= 1
    assert admin_page.get_by_text("Market Catalog Studio", exact=True).is_visible()
    assert admin_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    admin_page.screenshot(path=str(out / "market-catalog-dark-mobile.png"), full_page=True)
    admin_page.evaluate("localStorage.setItem('kofad-theme','light'); document.documentElement.dataset.theme='light'")

    # A sale-history row opens from its body, not only from the receipt reference.
    admin_page.goto("http://127.0.0.1:8000/documents/?kind=sale")
    transaction_rows = admin_page.locator("tr.transaction-row")
    if transaction_rows.count():
        target = transaction_rows.first.get_attribute("data-row-href")
        transaction_rows.first.locator("td").nth(2).click()
        admin_page.wait_for_url("http://127.0.0.1:8000" + target)

    admin_page.set_viewport_size({"width":1440,"height":1000})
    admin_page.screenshot(path=str(out / "branch-comparison-desktop.png"),full_page=True)
    admin_page.goto(staff_login_url)
    assert admin_page.get_by_label("Username, mobile number or verified email", exact=True).is_visible()
    staff_state = admin_page.request.get("http://127.0.0.1:8000/session/state/").json()
    assert staff_state["authenticated"] is False
    assert not errors, errors
    browser.close()
print("Readable light/dark design system, live-product homepage hero, movable Approval Center, redesigned Customer Inbox and Market Catalog, responsive staff/customer layouts, delivery map checkout, verified order IDs, sales checkout, debt payment, stock counts and transfers passed.")
