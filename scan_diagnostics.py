"""Website diagnostics scanner for www.beiyutitanium.com"""
import asyncio
import json
import time
from datetime import datetime
from playwright.async_api import async_playwright

TARGET = "https://www.beiyutitanium.com"

MOBILE_DEVICE = {
    "viewport": {"width": 390, "height": 844},
    "user_agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "device_scale_factor": 3,
    "is_mobile": True,
    "has_touch": True,
}


async def collect_page_data(page, context, label: str) -> dict:
    """Run all checks on the current page state."""
    console_errors = []
    failed_requests = []
    page.on("console", lambda msg: console_errors.append(
        {"type": msg.type, "text": msg.text}
    ) if msg.type in ("error", "warning") else None)
    page.on("requestfailed", lambda req: failed_requests.append(
        {"url": req.url, "failure": req.failure}
    ))

    t0 = time.time()
    try:
        response = await page.goto(TARGET, wait_until="networkidle", timeout=30000)
        load_time = round(time.time() - t0, 2)
        status = response.status if response else None
    except Exception as e:
        return {"label": label, "fatal": str(e)}

    perf = await page.evaluate("""() => {
        const t = performance.timing;
        const nav = performance.getEntriesByType('navigation')[0] || {};
        return {
            dns:           t.domainLookupEnd - t.domainLookupStart,
            tcp:           t.connectEnd - t.connectStart,
            ttfb:          t.responseStart - t.requestStart,
            dom_loaded:    t.domContentLoadedEventEnd - t.navigationStart,
            full_load:     t.loadEventEnd - t.navigationStart,
            transfer_size: nav.transferSize || 0,
            encoded_body:  nav.encodedBodySize || 0,
        };
    }""")

    seo = await page.evaluate("""() => {
        const getMeta = (name) => {
            const el = document.querySelector(
                `meta[name="${name}"], meta[property="${name}"]`
            );
            return el ? el.getAttribute('content') : null;
        };
        const imgs = Array.from(document.images);
        return {
            title:        document.title,
            description:  getMeta('description'),
            og_title:     getMeta('og:title'),
            og_image:     getMeta('og:image'),
            canonical:    document.querySelector('link[rel="canonical"]')
                            ?.getAttribute('href') || null,
            h1_count:     document.querySelectorAll('h1').length,
            h1_text:      document.querySelector('h1')?.innerText?.trim() || null,
            img_total:    imgs.length,
            img_no_alt:   imgs.filter(i => !i.alt).length,
            lang:         document.documentElement.lang || null,
            has_viewport: !!document.querySelector('meta[name="viewport"]'),
            viewport_content: document.querySelector('meta[name="viewport"]')
                            ?.getAttribute('content') || null,
        };
    }""")

    mobile = await page.evaluate("""() => {
        const allText  = Array.from(document.querySelectorAll('p, li, span, div'))
            .map(el => parseFloat(window.getComputedStyle(el).fontSize))
            .filter(s => s > 0);
        const minFont  = allText.length ? Math.min(...allText) : null;

        // Touch targets: anchors & buttons with tiny bounding boxes
        const targets  = Array.from(document.querySelectorAll('a, button, [role="button"]'));
        const tinyTargets = targets.filter(el => {
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && (r.width < 44 || r.height < 44);
        }).map(el => ({
            tag:  el.tagName,
            text: (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 40),
            w:    Math.round(el.getBoundingClientRect().width),
            h:    Math.round(el.getBoundingClientRect().height),
        }));

        // Horizontal overflow → causes mobile scroll bleed
        const bodyWidth    = document.body.scrollWidth;
        const windowWidth  = window.innerWidth;
        const hasHorizScroll = bodyWidth > windowWidth + 5;

        // Fixed-width elements wider than viewport
        const allEls   = Array.from(document.querySelectorAll('*'));
        const wideEls  = allEls.filter(el => {
            const r = el.getBoundingClientRect();
            return r.width > windowWidth + 5;
        }).slice(0, 5).map(el => ({
            tag: el.tagName,
            cls: el.className?.toString().slice(0, 40),
            w:   Math.round(el.getBoundingClientRect().width),
        }));

        // Media-query presence
        const hasMediaQueries = Array.from(document.styleSheets).some(ss => {
            try {
                return Array.from(ss.cssRules || []).some(
                    r => r instanceof CSSMediaRule
                );
            } catch { return false; }
        });

        // Input fields too small
        const inputs = Array.from(document.querySelectorAll('input, textarea, select'));
        const smallInputs = inputs.filter(el => {
            const r = el.getBoundingClientRect();
            return r.height > 0 && r.height < 44;
        }).length;

        // Tap-highlight / user-select issues (cosmetic)
        const hasFlash  = !!document.querySelector('object[type*="flash"], embed[type*="flash"]');
        const hasPopups = !!document.querySelector('[class*="popup"],[id*="popup"],[class*="modal"]');

        return {
            min_font_size:    minFont,
            tiny_tap_targets: tinyTargets.slice(0, 10),
            tiny_targets_total: tinyTargets.length,
            has_horiz_scroll: hasHorizScroll,
            body_scroll_width: bodyWidth,
            window_width:     windowWidth,
            wide_elements:    wideEls,
            has_media_queries: hasMediaQueries,
            small_inputs:     smallInputs,
            has_flash:        hasFlash,
            has_popups:       hasPopups,
        };
    }""")

    links = await page.evaluate("""() =>
        Array.from(document.links).map(a => ({
            href: a.href,
            text: a.innerText.trim().slice(0, 60),
            external: !a.href.startsWith(location.origin)
        }))
    """)
    internal_links = [l for l in links if not l["external"]]
    external_links = [l for l in links if l["external"]]

    broken_links = []
    check_links = list({l["href"] for l in internal_links if l["href"].startswith("http")})[:10]
    for href in check_links:
        try:
            r = await context.request.get(href, timeout=8000)
            if r.status >= 400:
                broken_links.append({"url": href, "status": r.status})
        except Exception as ex:
            broken_links.append({"url": href, "error": str(ex)})

    return {
        "label":           label,
        "http_status":     status,
        "load_time_sec":   load_time,
        "performance":     perf,
        "seo":             seo,
        "mobile":          mobile,
        "links": {
            "total":    len(links),
            "internal": len(internal_links),
            "external": len(external_links),
            "broken":   broken_links,
        },
        "console_errors":   console_errors,
        "failed_requests":  failed_requests,
    }


async def scan(url: str) -> dict:
    results = {"url": url, "scan_time": datetime.now().isoformat(), "desktop": {}, "mobile": {}, "summary": {}}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        # ── Desktop ───────────────────────────────────────────────────────
        print("[1/3] Desktop scan ...")
        ctx_desktop = await browser.new_context(viewport={"width": 1280, "height": 800}, ignore_https_errors=True)
        pg_desktop  = await ctx_desktop.new_page()
        desktop_data = await collect_page_data(pg_desktop, ctx_desktop, "desktop")
        await pg_desktop.screenshot(path="screenshot_desktop.png", full_page=True)
        await ctx_desktop.close()

        # ── Mobile ────────────────────────────────────────────────────────
        print("[2/3] Mobile scan (iPhone 14 Pro emulation) ...")
        ctx_mobile = await browser.new_context(**MOBILE_DEVICE, ignore_https_errors=True)
        pg_mobile  = await ctx_mobile.new_page()
        mobile_data = await collect_page_data(pg_mobile, ctx_mobile, "mobile")
        await pg_mobile.screenshot(path="screenshot_mobile.png", full_page=True)
        await pg_mobile.set_viewport_size({"width": 390, "height": 844})
        await pg_mobile.screenshot(path="screenshot_mobile_viewport.png", full_page=False)
        await ctx_mobile.close()

        await browser.close()

    results["desktop"] = desktop_data
    results["mobile"]  = mobile_data

    # ── Build issues list ─────────────────────────────────────────────────
    print("[3/3] Analysing results ...")
    issues = {"desktop": [], "mobile": [], "shared": []}

    def _shared(d, m, msg): issues["shared"].append(msg)
    def _mob(msg):          issues["mobile"].append(msg)
    def _desk(msg):         issues["desktop"].append(msg)

    for label, data, bucket in [("Desktop", desktop_data, issues["desktop"]),
                                 ("Mobile",  mobile_data,  issues["mobile"])]:
        if data.get("fatal"):
            bucket.append(f"Fatal: {data['fatal']}")
            continue
        p2 = data.get("performance", {})
        seo = data.get("seo", {})
        lnk = data.get("links", {})
        if data.get("http_status", 200) >= 400:
            bucket.append(f"HTTP {data['http_status']}")
        if p2.get("ttfb", 0) > 800:
            bucket.append(f"Slow TTFB: {p2['ttfb']}ms")
        if p2.get("full_load", 0) > 5000:
            bucket.append(f"Slow full load: {p2['full_load']}ms")
        if not seo.get("title"):
            bucket.append("Missing <title>")
        if not seo.get("description"):
            bucket.append("Missing meta description")
        if seo.get("h1_count", 0) == 0:
            bucket.append("No <h1> tag")
        if seo.get("h1_count", 0) > 1:
            bucket.append(f"Multiple <h1> ({seo['h1_count']})")
        if seo.get("img_no_alt", 0) > 0:
            bucket.append(f"{seo['img_no_alt']} images missing alt")
        if not seo.get("lang"):
            bucket.append("Missing <html lang>")
        if lnk.get("broken"):
            bucket.append(f"{len(lnk['broken'])} broken link(s)")
        errs = [e for e in data.get("console_errors", []) if e["type"] == "error"]
        if errs:
            bucket.append(f"{len(errs)} JS console error(s)")
        if data.get("failed_requests"):
            bucket.append(f"{len(data['failed_requests'])} failed request(s)")

    # Mobile-specific checks
    mob = mobile_data.get("mobile", {})
    seo_mob = mobile_data.get("seo", {})
    if not seo_mob.get("has_viewport"):
        issues["mobile"].append("Missing viewport meta tag")
    else:
        vc = seo_mob.get("viewport_content", "")
        if "user-scalable=no" in (vc or ""):
            issues["mobile"].append("viewport blocks user zoom (user-scalable=no)")
        if "maximum-scale=1" in (vc or ""):
            issues["mobile"].append("viewport clamps zoom (maximum-scale=1)")
    if mob.get("has_horiz_scroll"):
        issues["mobile"].append(
            f"Horizontal scroll overflow: body={mob['body_scroll_width']}px > viewport={mob['window_width']}px"
        )
    if mob.get("wide_elements"):
        for el in mob["wide_elements"]:
            issues["mobile"].append(f"Element wider than viewport: <{el['tag']} class=\"{el['cls']}\"> {el['w']}px")
    if not mob.get("has_media_queries"):
        issues["mobile"].append("No CSS media queries detected (may not be responsive)")
    tiny = mob.get("tiny_targets_total", 0)
    if tiny > 0:
        issues["mobile"].append(f"{tiny} tap target(s) smaller than 44×44px")
    if mob.get("small_inputs", 0) > 0:
        issues["mobile"].append(f"{mob['small_inputs']} input field(s) height < 44px")
    font = mob.get("min_font_size")
    if font and font < 12:
        issues["mobile"].append(f"Minimum font size {font}px (< 12px, hard to read on mobile)")
    if mob.get("has_flash"):
        issues["mobile"].append("Flash content detected (not supported on mobile)")

    total = len(issues["desktop"]) + len(issues["mobile"]) + len(issues["shared"])
    results["summary"] = {
        "total_issues": total,
        "issues": issues,
        "screenshots": ["screenshot_desktop.png", "screenshot_mobile.png", "screenshot_mobile_viewport.png"],
    }
    return results


def print_report(results: dict):
    url  = results["url"]
    desk = results.get("desktop", {})
    mob  = results.get("mobile", {})
    iss  = results.get("summary", {}).get("issues", {})

    print("\n" + "=" * 62)
    print(f"  DIAGNOSTICS REPORT — {url}")
    print("=" * 62)

    for label, data in [("DESKTOP (1280px)", desk), ("MOBILE  (390px iPhone)", mob)]:
        if data.get("fatal"):
            print(f"\n[{label}] FATAL: {data['fatal']}")
            continue
        perf = data.get("performance", {})
        seo  = data.get("seo", {})
        mob2 = data.get("mobile", {})
        print(f"\n{'─'*20} {label} {'─'*20}")
        print(f"  HTTP status    : {data.get('http_status')}")
        print(f"  Load time      : {data.get('load_time_sec')}s")
        print(f"  TTFB           : {perf.get('ttfb')}ms")
        print(f"  DOM loaded     : {perf.get('dom_loaded')}ms")
        print(f"  Full load      : {perf.get('full_load')}ms")
        print(f"  Transfer size  : {round(perf.get('transfer_size',0)/1024,1)} KB")
        print(f"  Title          : {seo.get('title','(missing)')}")
        print(f"  Description    : {seo.get('description','(missing)')}")
        print(f"  H1             : {seo.get('h1_text','(none)')}  [{seo.get('h1_count',0)} found]")
        print(f"  Viewport meta  : {seo.get('viewport_content','MISSING')}")
        print(f"  Images/no-alt  : {seo.get('img_no_alt',0)} / {seo.get('img_total',0)}")
        if label.startswith("MOBILE"):
            print(f"  Media queries  : {'yes' if mob2.get('has_media_queries') else 'NO'}")
            print(f"  Horiz scroll   : {'YES ⚠' if mob2.get('has_horiz_scroll') else 'no'}")
            print(f"  Min font size  : {mob2.get('min_font_size')}px")
            print(f"  Tiny tap targets: {mob2.get('tiny_targets_total',0)}")
            if mob2.get("tiny_tap_targets"):
                for t in mob2["tiny_tap_targets"][:5]:
                    print(f"    • <{t['tag']}> \"{t['text']}\" {t['w']}×{t['h']}px")
        lnk = data.get("links", {})
        print(f"  Links (in/ext) : {lnk.get('internal',0)} / {lnk.get('external',0)}")
        if lnk.get("broken"):
            for bl in lnk["broken"]:
                print(f"    ✗ {bl['url']} [{bl.get('status','ERR')}]")

    print(f"\n{'─'*20} ISSUE SUMMARY {'─'*20}")
    total = results["summary"].get("total_issues", 0)
    print(f"  Total issues: {total}")
    for cat in ("shared", "desktop", "mobile"):
        lst = iss.get(cat, [])
        if lst:
            print(f"\n  [{cat.upper()}]")
            for i in lst:
                print(f"    • {i}")

    print(f"\nReport saved  : diagnostics_report.json")
    print(f"Screenshots   : screenshot_desktop.png  |  screenshot_mobile.png  |  screenshot_mobile_viewport.png")


async def main():
    results = await scan(TARGET)
    with open("diagnostics_report.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print_report(results)


asyncio.run(main())
