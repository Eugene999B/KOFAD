"""Read-only public KOFAD QA snapshot: exact 375px + simulated Slow 3G.
No sign-ins, no forms submitted, no checkout, no production writes.
"""
import base64, json, re, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from playwright.sync_api import sync_playwright

out = Path("qa-artifacts")
out.mkdir(exist_ok=True)
BASE = "https://kofadimpex.com"
PAGES = [
 ("home", "/"), ("market", "/market/"),
 ("product-1", "/market/products/1/"),
 ("product-2", "/market/products/2/"),
 ("wholesale", "/wholesale/"), ("about", "/about/"),
 ("contact", "/contact/"), ("delivery", "/delivery/"),
 ("returns", "/returns-policy/"), ("faq", "/faq/"),
 ("messages", "/market/messages/"), ("access", "/market/access/"),
 ("search-empty", "/market/?q=" + quote("qasafenoresult20261024")),
 ("search-punctuation", "/market/?q=" + quote("!@#$%")),
 ("staff-login", "https://staff.kofadimpex.com/"),
]
results = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
    ctx = browser.new_context(
        viewport={"width":375, "height":812}, device_scale_factor=1,
        is_mobile=True, has_touch=True, locale="en-GH",
        reduced_motion="reduce",
    )
    page = ctx.new_page()
    cdps = ctx.new_cdp_session(page)
    cdps.send("Network.enable")
    def screenshot_cdp(path):
        """Chromium native PNG capture; avoids Playwright waiting for remote fonts."""
        payload = cdps.send("Page.captureScreenshot", {
            "format":"png", "captureBeyondViewport":True,
            "fromSurface":True,
        })
        Path(path).write_bytes(base64.b64decode(payload["data"]))
    for n, (name,path) in enumerate(PAGES):
        console=[]; errors=[]; failed=[]; statuses=[]
        def on_console(msg):
            if msg.type == "error": console.append(msg.text[:400])
        def on_pageerror(err): errors.append(str(err)[:500])
        def on_failed(request): failed.append((request.url, request.failure))
        def on_response(response):
            if response.status >= 400: statuses.append((response.status, response.url))
        page.on("console",on_console)
        page.on("pageerror",on_pageerror)
        page.on("requestfailed",on_failed)
        page.on("response",on_response)
        slow = name in ("home", "market", "product-1", "access")
        cdps.send("Network.emulateNetworkConditions", {
          "offline":False, "latency":350 if slow else 0,
          "downloadThroughput":60*1024 if slow else -1,
          "uploadThroughput":30*1024 if slow else -1,
        })
        url=path if path.startswith("https://") else BASE+path
        outcome={"name":name, "url":url,"view_width":375,"slow_3g_emulated":slow}
        try:
            response=page.goto(url,wait_until="domcontentloaded",timeout=45000)
            outcome["status"]=response.status if response else None
            page.wait_for_timeout(1000 if not slow else 2500)
            outcome["title"]=page.title()
            outcome["final_url"]=page.url
            outcome.update(page.evaluate("""() => ({
              scroll_width:document.documentElement.scrollWidth,
              client_width:document.documentElement.clientWidth,
              page_height:document.documentElement.scrollHeight,
              h1:[...document.querySelectorAll('h1')].slice(0,3).map(x=>x.textContent.trim().slice(0,160)),
              mobile_menu_exists:!!document.querySelector('.public-mobile-menu'),
              mobile_menu_display:document.querySelector('.public-mobile-menu') ? getComputedStyle(document.querySelector('.public-mobile-menu')).display : null,
              desktop_nav_display:document.querySelector('.public-desktop-nav') ? getComputedStyle(document.querySelector('.public-desktop-nav')).display : null,
              broken_images:[...document.images].filter(img=>img.complete && img.naturalWidth===0).map(img=>img.src.slice(0,160)).slice(0,12),
              images_total:document.images.length,
              image_urls:[...document.images].slice(0,5).map(img=>img.currentSrc.slice(0,160)),
              missing_alt_count:[...document.images].filter(img=>!img.hasAttribute('alt')).length,
              visible_buttons:[...document.querySelectorAll('button')].filter(x=>x.getBoundingClientRect().height && x.getBoundingClientRect().width).slice(0,12).map(x=>x.textContent.trim().slice(0,65)),
            })"""))
            if name == "home":
                panel=page.locator("#kofad-cookie-panel")
                outcome["cookie_visible_initially"] = bool(panel.count() and panel.is_visible())
                if outcome["cookie_visible_initially"]:
                    screenshot_cdp(out/"home-cookie-consent-375.png")
                    essential=page.get_by_role("button",name="Essential only")
                    if essential.count():
                        try:
                            essential.click(timeout=5000)
                            outcome["cookie_dismissed"]=not panel.is_visible()
                        except Exception as exc:
                            outcome["cookie_dismiss_error"]=str(exc)[:250]
                mobile=page.locator(".public-mobile-menu summary")
                if mobile.count() and mobile.is_visible():
                    try:
                        mobile.click(timeout=4500)
                        outcome["mobile_menu_open"]=page.locator(".public-mobile-menu").evaluate("(el) => el.open")
                        outcome["mobile_menu_text"]=page.locator(".public-mobile-menu").inner_text()[:400]
                        screenshot_cdp(out/"home-menu-375.png")
                        mobile.click(timeout=3000)
                    except Exception as exc:
                        outcome["menu_interaction_error"]=str(exc)[:350]
            # Capture every screen directly through Chromium, including slow-font pages.
            screenshot_cdp(out/f"{n+1:02d}-{name}-375.png")
        except Exception as exc:
            outcome["error"]=str(exc)[:550]
            try:screenshot_cdp(out/f"{n+1:02d}-{name}-ERROR.png")
            except Exception:pass
        outcome["console_errors"]=console[:15]
        outcome["uncaught_exceptions"]=errors[:15]
        outcome["failed_requests"]=failed[:15]
        outcome["http_error_resources"]=statuses[:15]
        results.append(outcome)
        print("KOFAD_QA_PAGE "+json.dumps(outcome,ensure_ascii=False),flush=True)
        page.remove_listener("console",on_console)
        page.remove_listener("pageerror",on_pageerror)
        page.remove_listener("requestfailed",on_failed)
        page.remove_listener("response",on_response)
    browser.close()
(Path("qa-artifacts")/"index.json").write_text(json.dumps({
    "date_utc":datetime.now(timezone.utc).isoformat(),
    "safety":"Read-only GET navigation; no submissions or authentication",
    "viewport":"375x812",
    "slow_profile":"Simulated on home, market, product 1, access (350ms RTT; ~480kbps download)",
    "pages":results,
},indent=2,ensure_ascii=False),encoding="utf-8")
