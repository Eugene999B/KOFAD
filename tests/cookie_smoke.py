"""Consent browser QA: no sticky footer, non-blocking login and persisted choice."""
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ARTIFACTS = Path("test-results")
ARTIFACTS.mkdir(exist_ok=True)
BASE = "http://127.0.0.1:8000"


def no_horizontal_overflow(page):
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    ), "Cookie UI introduced horizontal overflow"


def login_button_accessible(page):
    """Clipped form containers must not hide the sign-in button at any height."""
    page.goto(BASE + "/login/")
    form = page.locator(".premium-login-card")
    button = page.get_by_role("button", name="Sign in to workspace")
    expect(form).to_be_visible()
    expect(button).to_be_visible()
    assert page.locator(".premium-login-card").evaluate(
        "el => getComputedStyle(el).overflowY !== 'hidden'"
    ), "Login card still clips its primary action"
    assert page.evaluate(
        "document.documentElement.scrollHeight >= document.documentElement.clientHeight"
    )
    button.scroll_into_view_if_needed()
    rect = button.bounding_box()
    assert rect is not None
    assert rect["height"] >= 44
    assert rect["y"] >= -2 and rect["y"] + rect["height"] <= page.viewport_size["height"] + 2, (
        "Login submit action is not reachable within the scrollable viewport"
    )
    no_horizontal_overflow(page)


with sync_playwright() as playwright:
    browser = playwright.chromium.launch()

    # No optional consent selected yet: the banner is visible but there is no
    # persistent page-bottom 'Cookie choices' line.
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    errors = []
    trackers = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "request", lambda request: trackers.append(request.url)
        if "google-analytics.com" in request.url
        or "googletagmanager.com" in request.url else None
    )
    page.goto(BASE + "/")
    panel = page.locator("[data-cookie-panel]")
    expect(panel).to_be_visible()
    assert "A better visit, your choice." in panel.inner_text()
    assert page.locator(".kofad-cookie-footer").count() == 0
    assert page.locator("[data-cookie-open]").count() == 0
    no_horizontal_overflow(page)
    page.screenshot(path=str(ARTIFACTS / "cookie-banner-mobile.png"), full_page=True)

    # Reject optional categories; essential login/CSRF/session cookies remain
    # part of normal Django processing and authentication remains accessible.
    page.locator("[data-cookie-reject]").click()
    expect(panel).to_be_hidden()
    page.reload()
    expect(panel).to_be_hidden()
    assert page.locator("[data-cookie-open]").count() == 0
    login_button_accessible(page)

    # A visible privacy-page setting remains reachable, without a permanent
    # floating launcher on the public and staff login pages.
    page.goto(BASE + "/privacy/")
    expect(panel).to_be_hidden()
    launcher = page.locator("[data-cookie-open]")
    expect(launcher).to_be_visible()
    launcher.click()
    expect(panel).to_be_visible()
    expect(page.locator("[name=cookie_preferences]")).not_to_be_checked()
    expect(page.locator("[name=cookie_analytics]")).to_be_disabled()
    page.locator("[name=cookie_preferences]").check()
    page.locator("[data-cookie-save]").click()
    expect(panel).to_be_hidden()
    assert launcher.get_attribute("aria-expanded") == "false"

    # Accepted preference storage survives navigation, but consent UI vanishes.
    page.goto(BASE + "/market/access/")
    page.locator("[data-theme-toggle]:visible").first.click()
    selected = page.locator("html").get_attribute("data-theme")
    page.reload()
    assert page.locator("html").get_attribute("data-theme") == selected
    expect(panel).to_be_hidden()

    # Withdrawal from privacy page removes saved optional preferences.
    page.goto(BASE + "/privacy/")
    launcher.click()
    expect(page.locator("[name=cookie_preferences]")).to_be_checked()
    page.screenshot(path=str(ARTIFACTS / "cookie-preferences-mobile.png"), full_page=True)
    page.locator("[data-cookie-reject]").click()
    assert page.evaluate("localStorage.getItem('kofad-theme')") is None
    page.reload()
    expect(panel).to_be_hidden()
    no_horizontal_overflow(page)
    assert not errors, errors
    assert not trackers, trackers
    context.close()

    # Accept and reject are independently tested on clean first visits.
    accepted = browser.new_context(viewport={"width": 1280, "height": 800})
    accepted_page = accepted.new_page()
    accepted_page.goto(BASE + "/")
    expect(accepted_page.locator("[data-cookie-panel]")).to_be_visible()
    accepted_page.locator("[data-cookie-accept]").click()
    expect(accepted_page.locator("[data-cookie-panel]")).to_be_hidden()
    accepted_page.reload()
    expect(accepted_page.locator("[data-cookie-panel]")).to_be_hidden()
    accepted.close()

    # Desktop staff login at the screenshot height must not clip the button.
    desktop = browser.new_context(viewport={"width": 1600, "height": 900})
    staff = desktop.new_page()
    staff.goto(BASE + "/login/")
    expect(staff.locator("[data-cookie-panel]")).to_be_visible()
    # Banner is at lower left, not on top of sign-in controls at the right.
    overlap = staff.evaluate("""() => {
        const banner = document.querySelector('[data-cookie-panel]').getBoundingClientRect();
        const button = document.querySelector('.premium-login-submit').getBoundingClientRect();
        return banner.left < button.right && banner.right > button.left
            && banner.top < button.bottom && banner.bottom > button.top;
    }""")
    assert not overlap, "The cookie panel overlaps the staff submit button"
    staff.locator("[data-cookie-reject]").click()
    login_button_accessible(staff)
    staff.screenshot(path=str(ARTIFACTS / "staff-login-unclipped-desktop.png"), full_page=True)
    desktop.close()

    # Real isolated-fixture login after rejecting optional cookies proves
    # optional analytics/preferences are not prerequisites for a staff session.
    # The CI runner seeds this account; it is not a production credential.
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.conf import settings

    private = browser.new_context(viewport={"width": 1280, "height": 900})
    signed_in = private.new_page()
    signed_in.goto(BASE + "/" + settings.STAFF_LOGIN_SLUG + "/")
    expect(signed_in.locator("[data-cookie-panel]")).to_be_visible()
    signed_in.locator("[data-cookie-reject]").click()
    signed_in.get_by_label("Username, mobile number or verified email", exact=True).fill("demo")
    signed_in.get_by_label("Password", exact=True).fill("isolated-demo-browser-password")
    signed_in.get_by_role("button", name="Sign in to workspace").click()
    signed_in.wait_for_url(BASE + "/workspace/")
    expect(signed_in.get_by_role("heading", name="Command centre", exact=True)).to_be_visible()
    expect(signed_in.locator("[data-cookie-panel]")).to_be_hidden()
    private.close()
    browser.close()
