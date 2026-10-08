"""Browser regression for cookie choices, persisted preferences and mobile layout."""
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ARTIFACTS = Path("test-results")
ARTIFACTS.mkdir(exist_ok=True)
with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    errors = []
    trackers = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: trackers.append(request.url) if
            "google-analytics.com" in request.url or "googletagmanager.com" in request.url else None)
    page.goto("http://127.0.0.1:8000/")
    panel = page.locator("[data-cookie-panel]")
    expect(panel).to_be_visible()
    page.locator("[data-cookie-reject]").click()
    expect(panel).to_be_hidden()
    page.reload()
    expect(panel).to_be_hidden()
    page.locator("[data-cookie-open]").click()
    expect(page.locator("[name=cookie_preferences]")).not_to_be_checked()
    expect(page.locator("[name=cookie_analytics]")).to_be_disabled()
    page.locator("[name=cookie_preferences]").check()
    page.locator("[data-cookie-save]").click()
    page.goto("http://127.0.0.1:8000/market/access/")
    page.locator("[data-theme-toggle]:visible").first.click()
    selected = page.locator("html").get_attribute("data-theme")
    page.reload()
    assert page.locator("html").get_attribute("data-theme") == selected
    page.locator("[data-cookie-open]").click()
    expect(page.locator("[name=cookie_preferences]")).to_be_checked()
    page.screenshot(path=str(ARTIFACTS / "cookie-choices-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.locator("[data-cookie-reject]").click()
    assert page.evaluate("localStorage.getItem('kofad-theme')") is None
    page.reload()
    expect(panel).to_be_hidden()
    assert not errors, errors
    assert not trackers, trackers
    context.close()
    browser.close()
