"""M0: discover streaming endpoints behind https://web.seattle.gov/Travelers/#

Drives the page headless with a network listener, iterates the neighborhood
selector, clicks cameras to trigger the JW Player, and interrogates
window.jwplayer for the real .m3u8 source. Everything observed goes to
docs/network-trace.jsonl; conclusions go to stdout as JSON.
"""
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
TRACE = ROOT / "docs" / "network-trace.jsonl"
PROXY = "http://127.0.0.1:18888"

INTERESTING = (".m3u8", ".ts", ".mpd", ".mp4", "/Travelers/")


async def main() -> None:
    trace_f = TRACE.open("w", encoding="utf-8")
    findings: dict = {"json_endpoints": [], "media_urls": [], "jwplayer": None}
    seen: set[str] = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, proxy={"server": PROXY}
        )
        ctx = await browser.new_context(ignore_https_errors=True)
        page = await ctx.new_page()

        def log_req(req):
            rec = {"kind": "request", "method": req.method, "url": req.url}
            trace_f.write(json.dumps(rec) + "\n")

        async def log_resp(resp):
            ct = resp.headers.get("content-type", "")
            rec = {
                "kind": "response",
                "status": resp.status,
                "url": resp.url,
                "content_type": ct,
            }
            trace_f.write(json.dumps(rec) + "\n")
            url = resp.url
            if url in seen:
                return
            seen.add(url)
            if ".m3u8" in url or ".mpd" in url or "mixed-replace" in ct:
                findings["media_urls"].append({"url": url, "content_type": ct, "status": resp.status})
            elif "json" in ct and "/Travelers/" in url:
                try:
                    body = await resp.text()
                except Exception:
                    body = None
                findings["json_endpoints"].append(
                    {"url": url, "sample": (body or "")[:2000]}
                )

        page.on("request", log_req)
        page.on("response", lambda r: asyncio.ensure_future(log_resp(r)))

        await page.goto("https://web.seattle.gov/Travelers/", timeout=60000)
        await page.wait_for_timeout(4000)

        # Dump selects present on the page so we know the control names.
        selects = await page.evaluate(
            """() => [...document.querySelectorAll('select')].map(s => ({
                id: s.id, name: s.name,
                options: [...s.options].map(o => ({value: o.value, text: o.text}))
            }))"""
        )
        findings["selects"] = selects

        # Iterate every option of every select to fire the per-neighborhood
        # inventory calls; stop early once we've seen a few JSON endpoints.
        for sel in selects:
            ident = sel["id"] or sel["name"]
            if not ident:
                continue
            for opt in sel["options"]:
                if not opt["value"]:
                    continue
                try:
                    await page.select_option(f"#{sel['id']}" if sel["id"] else f"select[name='{sel['name']}']", opt["value"])
                    await page.wait_for_timeout(1200)
                except Exception as exc:
                    trace_f.write(json.dumps({"kind": "error", "select": ident, "opt": opt["value"], "err": str(exc)}) + "\n")
                if len(findings["json_endpoints"]) >= 3:
                    break
            if len(findings["json_endpoints"]) >= 3:
                break

        # Try to trigger a video player: click the first thing that looks
        # like a camera link/marker.
        clicked = False
        for selector in [
            "a[onclick*='amera']", "a[href*='amera']", ".cameraLink",
            "area", "img[src*='camera']", "td a", "li a",
        ]:
            els = await page.query_selector_all(selector)
            if els:
                try:
                    await els[0].click(timeout=3000)
                    clicked = True
                    await page.wait_for_timeout(4000)
                    break
                except Exception:
                    continue
        findings["clicked"] = clicked

        # Ask JW Player directly.
        try:
            jw = await page.evaluate(
                """() => {
                    try {
                        const p = window.jwplayer && window.jwplayer();
                        if (!p || !p.getPlaylist) return {present: !!window.jwplayer};
                        return {
                            present: true,
                            playlist: p.getPlaylist ? p.getPlaylist() : null,
                            item: p.getPlaylistItem ? p.getPlaylistItem() : null,
                        };
                    } catch (e) { return {error: String(e)}; }
                }"""
            )
            findings["jwplayer"] = jw
        except Exception as exc:
            findings["jwplayer"] = {"error": str(exc)}

        await page.wait_for_timeout(3000)
        await browser.close()

    trace_f.close()
    print(json.dumps(findings, indent=2)[:12000])


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
