"""M0 pass 2: open the Camera List, pick a neighborhood, click cameras,
capture the JW Player manifest URL + any m3u8 network traffic."""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
PROXY = "http://127.0.0.1:18888"


async def main() -> None:
    findings: dict = {"media_urls": [], "camera_list_html": None, "jw": [], "requests": []}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, proxy={"server": PROXY})
        ctx = await browser.new_context(ignore_https_errors=True)
        page = await ctx.new_page()

        def on_req(req):
            u = req.url
            if any(k in u for k in (".m3u8", ".ts", ".mpd", "stream", "video", "jwp")):
                findings["requests"].append(u)
                if ".m3u8" in u:
                    findings["media_urls"].append(u)

        page.on("request", on_req)

        await page.goto("https://web.seattle.gov/Travelers/", timeout=60000)
        await page.wait_for_timeout(3000)

        # Expand whatever accordion hides the camera list, if possible.
        try:
            toggler = await page.query_selector(
                "a:has-text('Camera'), button:has-text('Camera'), "
                "[data-toggle]:has-text('Camera')"
            )
            if toggler:
                await toggler.click(timeout=2000)
                await page.wait_for_timeout(1000)
        except Exception:
            pass

        # The select is hidden; drive it with JS and fire change events.
        await page.evaluate(
            """() => {
                const s = document.querySelector('#ddCameraNeighborhoods');
                const idx = [...s.options].findIndex(o => o.text === 'Downtown');
                s.selectedIndex = idx;
                s.dispatchEvent(new Event('change', {bubbles: true}));
            }"""
        )
        await page.wait_for_timeout(3000)

        # Dump whatever list appeared near the camera dropdown.
        html = await page.evaluate(
            """() => {
                const sel = document.querySelector('#ddCameraNeighborhoods');
                let n = sel.closest('div');
                for (let i = 0; i < 4 && n; i++) {
                    if (n.innerText && n.innerText.length > 200) break;
                    n = n.parentElement;
                }
                return n ? n.innerHTML.slice(0, 6000) : null;
            }"""
        )
        findings["camera_list_html"] = html

        # Click every plausible camera entry until a jwplayer shows up.
        candidates = await page.query_selector_all(
            "#cameraList a, .camera-list a, ul#cameras a, a[onclick*='Camera'], "
            "a[onclick*='camera'], div[id*='amera'] a, li a"
        )
        findings["n_candidates"] = len(candidates)
        for el in candidates[:12]:
            try:
                text = (await el.inner_text()).strip()[:60]
                await el.evaluate("el => el.click()")  # JS click: works on hidden elements
            except Exception:
                continue
            await page.wait_for_timeout(3500)
            jw = await page.evaluate(
                """() => {
                    try {
                        if (!window.jwplayer) return null;
                        const players = (jwplayer.api && jwplayer.api.getAllPlayers)
                            ? jwplayer.api.getAllPlayers() : [jwplayer()];
                        return players.map(p => ({
                            id: p && p.id,
                            item: p && p.getPlaylistItem ? p.getPlaylistItem() : null,
                        }));
                    } catch (e) { return {error: String(e)}; }
                }"""
            )
            # Also sweep for <video> elements and their sources
            vids = await page.evaluate(
                """() => [...document.querySelectorAll('video')].map(v => ({
                    src: v.src, sources: [...v.querySelectorAll('source')].map(s => s.src)
                }))"""
            )
            findings["jw"].append({"clicked": text, "jw": jw, "videos": vids})
            if findings["media_urls"]:
                break
            # close any modal so next click works
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(500)
            except Exception:
                pass

        await browser.close()

    print(json.dumps(findings, indent=2)[:14000])


if __name__ == "__main__":
    asyncio.run(main())
