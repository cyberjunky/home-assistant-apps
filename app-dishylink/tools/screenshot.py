import sys, json
from playwright.sync_api import sync_playwright

out, theme, width, height = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
steps = sys.argv[5:]
BASE = "http://127.0.0.1:18080"
import os
TLES = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tles.txt")).read()
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": width, "height": height}, device_scale_factor=1,
                        color_scheme=theme, locale="en-GB", timezone_id="Europe/Amsterdam")
    external = []
    def guard(route):
        url = route.request.url
        if url.startswith(BASE + "/celestrak/"):
            return route.fulfill(status=200, content_type="text/plain", body=TLES)
        if url.startswith(BASE) or url.startswith("https://fonts.googleapis.com/") or url.startswith("https://fonts.gstatic.com/"):
            return route.continue_()
        external.append(url)
        route.abort()
    ctx.route("**/*", guard)
    ctx.add_init_script("try { localStorage.setItem('dishylink-theme', '%s'); } catch (e) {}" % theme)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "/", wait_until="load")
    page.wait_for_timeout(6000)
    for step in steps:
        kind, _, arg = step.partition("=")
        if kind == "click":
            page.get_by_text(arg, exact=True).first.click()
        elif kind == "role":
            role, _, name = arg.partition(":")
            page.get_by_role(role, name=name).first.click(timeout=8000)
        elif kind == "css":
            page.locator(arg).first.click(timeout=8000)
        elif kind == "js":
            print("js click:", page.evaluate("""(label) => { const all = [...document.querySelectorAll('[aria-label],[title]')].filter(e => (e.getAttribute('aria-label') || e.getAttribute('title')) === label); const el = all.find(e => e.offsetParent !== null) || all[0]; if (!el) return 'not found'; el.click(); return el.tagName + ' of ' + all.length; }""", arg))
        elif kind == "wait":
            page.wait_for_timeout(int(arg))
        elif kind == "scroll":
            page.mouse.wheel(0, int(arg))
        elif kind == "dump":
            print(page.evaluate("() => [...document.querySelectorAll('button,a,[role=tab]')].map(e => (e.getAttribute('aria-label')||e.innerText||'').trim().slice(0,40)).filter(Boolean).join(' | ')"))
        elif kind == "ls":
            print(page.evaluate("() => JSON.stringify(Object.fromEntries(Object.entries(localStorage).map(([k,v]) => [k, v.slice(0,80)])))"))
        page.wait_for_timeout(800)
    page.screenshot(path=out, full_page=(height == 0))
    print("external blocked:", sorted(set(u.split('?')[0] for u in external)))
    print("errors:", errors[:5])
    b.close()
