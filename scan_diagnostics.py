"""Website diagnostics scanner for www.beiyutitanium.com"""
import asyncio
import json
import time
from datetime import datetime
from playwright.async_api import async_playwright

TARGET = "https://www.beiyutitanium.com"

async def scan(url: str) -> dict:
    results = {
        "url": url,
        "scan_time": datetime.now().isoformat(),
        "pages": [],
        "summary": {}
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (compatible; DiagnosticsBot/1.0)"
        )
        page = await context.new_page()

        console_errors = []
        failed_requests = []
        page.on("console", lambda msg: console_errors.append({
            "type": msg.type, "text": msg.text
        }) if msg.type in ("error", "warning") else None)
        page.on("requestfailed", lambda req: failed_requests.append({
            "url": req.url, "failure": req.failure
        }))

        # ── 1. Load main page ──────────────────────────────────────────────
        print(f"[1/5] Loading {url} ...")
        t0 = time.time()
        try:
            response = await page.goto(url, wait_until="networkidle", timeout=30000)
            load_time = round(time.time() - t0, 2)
            status = response.status if response else None
        except Exception as e:
            results["summary"]["fatal"] = str(e)
            await browser.close()
            return results

        # ── 2. Performance timing ──────────────────────────────────────────
        print("[2/5] Collecting performance metrics ...")
        perf = await page.evaluate("""() => {
            const t = performance.timing;
            const nav = performance.getEntriesByType('navigation')[0] || {};
            return {
                dns:        t.domainLookupEnd - t.domainLookupStart,
                tcp:        t.connectEnd - t.connectStart,
                ttfb:       t.responseStart - t.requestStart,
                dom_loaded: t.domContentLoadedEventEnd - t.navigationStart,
                full_load:  t.loadEventEnd - t.navigationStart,
                transfer_size: nav.transferSize || 0,
                encoded_body:  nav.encodedBodySize || 0,
            };
        }""")

        # ── 3. SEO / meta checks ───────────────────────────────────────────
        print("[3/5] Checking SEO & meta tags ...")
        seo = await page.evaluate("""() => {
            const getMeta = (name) => {
                const el = document.querySelector(
                    `meta[name="${name}"], meta[property="${name}"]`
                );
                return el ? el.getAttribute('content') : null;
            };
            const imgs = Array.from(document.images);
            return {
                title:       document.title,
                description: getMeta('description'),
                og_title:    getMeta('og:title'),
                og_image:    getMeta('og:image'),
                canonical:   document.querySelector('link[rel="canonical"]')
                                ?.getAttribute('href') || null,
                h1_count:    document.querySelectorAll('h1').length,
                h1_text:     document.querySelector('h1')?.innerText?.trim() || null,
                img_total:   imgs.length,
                img_no_alt:  imgs.filter(i => !i.alt).length,
                lang:        document.documentElement.lang || null,
                has_viewport: !!document.querySelector('meta[name="viewport"]'),
            };
        }""")

        # ── 4. Link audit ─────────────────────────────────────────────────
        print("[4/5] Auditing links ...")
        links = await page.evaluate("""() => {
            return Array.from(document.links).map(a => ({
                href: a.href,
                text: a.innerText.trim().slice(0, 60),
                external: !a.href.startsWith(location.origin)
            }));
        }""")

        internal_links = [l for l in links if not l["external"]]
        external_links = [l for l in links if l["external"]]

        # Spot-check up to 10 internal links
        broken_links = []
        check_links = list({l["href"] for l in internal_links if l["href"].startswith("http")})[:10]
        for href in check_links:
            try:
                r = await context.request.get(href, timeout=8000)
                if r.status >= 400:
                    broken_links.append({"url": href, "status": r.status})
            except Exception as ex:
                broken_links.append({"url": href, "error": str(ex)})

        # ── 5. Screenshot ─────────────────────────────────────────────────
        print("[5/5] Taking screenshot ...")
        screenshot_path = "/home/user/PennMa/screenshot_homepage.png"
        await page.screenshot(path=screenshot_path, full_page=True)

        # ── Mobile viewport check ─────────────────────────────────────────
        await page.set_viewport_size({"width": 375, "height": 812})
        mobile_screenshot = "/home/user/PennMa/screenshot_mobile.png"
        await page.screenshot(path=mobile_screenshot, full_page=False)

        await browser.close()

    # ── Assemble report ────────────────────────────────────────────────────
    page_data = {
        "http_status": status,
        "load_time_sec": load_time,
        "performance": perf,
        "seo": seo,
        "links": {
            "total": len(links),
            "internal": len(internal_links),
            "external": len(external_links),
            "broken": broken_links,
        },
        "console_errors": console_errors,
        "failed_requests": failed_requests,
    }
    results["pages"].append(page_data)

    # ── Human-readable summary ────────────────────────────────────────────
    issues = []
    if status and status >= 400:
        issues.append(f"HTTP {status} on homepage")
    if perf.get("ttfb", 0) > 800:
        issues.append(f"Slow TTFB: {perf['ttfb']}ms (>800ms)")
    if perf.get("full_load", 0) > 5000:
        issues.append(f"Slow full-load: {perf['full_load']}ms (>5s)")
    if not seo.get("description"):
        issues.append("Missing meta description")
    if not seo.get("title"):
        issues.append("Missing <title>")
    if seo.get("h1_count", 0) == 0:
        issues.append("No <h1> tag found")
    if seo.get("h1_count", 0) > 1:
        issues.append(f"Multiple <h1> tags ({seo['h1_count']})")
    if seo.get("img_no_alt", 0) > 0:
        issues.append(f"{seo['img_no_alt']} images missing alt text")
    if not seo.get("has_viewport"):
        issues.append("Missing viewport meta tag (mobile unfriendly)")
    if not seo.get("lang"):
        issues.append("Missing lang attribute on <html>")
    if broken_links:
        issues.append(f"{len(broken_links)} broken internal link(s) detected")
    errors_only = [e for e in console_errors if e["type"] == "error"]
    if errors_only:
        issues.append(f"{len(errors_only)} JS console error(s)")
    if failed_requests:
        issues.append(f"{len(failed_requests)} failed network request(s)")

    results["summary"] = {
        "issues_found": len(issues),
        "issues": issues,
        "screenshots": [screenshot_path, mobile_screenshot],
    }
    return results


async def main():
    results = await scan(TARGET)
    report_path = "/home/user/PennMa/diagnostics_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print(f"  DIAGNOSTICS REPORT — {results['url']}")
    print("=" * 60)
    page = results["pages"][0] if results["pages"] else {}
    perf = page.get("performance", {})
    seo  = page.get("seo", {})
    lnk  = page.get("links", {})

    print(f"\nHTTP Status    : {page.get('http_status')}")
    print(f"Load time      : {page.get('load_time_sec')}s")
    print(f"\n── Performance ─────────────────────────")
    print(f"  DNS lookup   : {perf.get('dns')}ms")
    print(f"  TCP connect  : {perf.get('tcp')}ms")
    print(f"  TTFB         : {perf.get('ttfb')}ms")
    print(f"  DOM loaded   : {perf.get('dom_loaded')}ms")
    print(f"  Full load    : {perf.get('full_load')}ms")
    print(f"  Transfer size: {round(perf.get('transfer_size',0)/1024,1)} KB")

    print(f"\n── SEO ─────────────────────────────────")
    print(f"  Title        : {seo.get('title','(missing)')}")
    print(f"  Description  : {seo.get('description','(missing)')}")
    print(f"  H1 tag       : {seo.get('h1_text','(none)')} ({seo.get('h1_count',0)} found)")
    print(f"  Lang attr    : {seo.get('lang','(missing)')}")
    print(f"  Canonical    : {seo.get('canonical','(none)')}")
    print(f"  Viewport     : {'✓' if seo.get('has_viewport') else '✗ MISSING'}")
    print(f"  Images w/o alt: {seo.get('img_no_alt',0)} / {seo.get('img_total',0)}")

    print(f"\n── Links ───────────────────────────────")
    print(f"  Total links  : {lnk.get('total',0)}")
    print(f"  Internal     : {lnk.get('internal',0)}")
    print(f"  External     : {lnk.get('external',0)}")
    if lnk.get("broken"):
        print(f"  Broken links : {len(lnk['broken'])}")
        for bl in lnk["broken"]:
            print(f"    ✗ {bl['url']} — {bl.get('status','ERR')}")

    errors = [e for e in page.get("console_errors",[]) if e["type"]=="error"]
    if errors:
        print(f"\n── Console Errors ({len(errors)}) ────────────────")
        for e in errors[:5]:
            print(f"  ✗ {e['text'][:100]}")

    if page.get("failed_requests"):
        print(f"\n── Failed Requests ({len(page['failed_requests'])}) ──────────────")
        for r in page["failed_requests"][:5]:
            print(f"  ✗ {r['url'][:80]}")

    summary = results.get("summary", {})
    print(f"\n── Summary ─────────────────────────────")
    print(f"  Issues found : {summary.get('issues_found', 0)}")
    for issue in summary.get("issues", []):
        print(f"    • {issue}")

    print(f"\nFull report saved to: {report_path}")
    print("Screenshots saved to: screenshot_homepage.png, screenshot_mobile.png")

asyncio.run(main())
