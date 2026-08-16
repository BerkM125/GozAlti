"""Verify adjustable panes: seam drag, feed pan, wheel zoom."""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
DEBUG = ROOT / "debug"


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1720, "height": 980})
        page.on("pageerror", lambda e: print("pageerror:", str(e)[:300]))
        await page.goto("http://127.0.0.1:8000/", timeout=30000)
        await page.wait_for_timeout(2500)
        await page.evaluate(
            """() => { [...document.querySelectorAll('.street-item')]
                 .find(e => e.textContent.includes('Galer')).click(); }"""
        )
        for _ in range(20):
            await page.wait_for_timeout(3000)
            if await page.query_selector(".pane"):
                break
        await page.wait_for_timeout(8000)

        # 1. drag the seam up 120px
        grab = await page.query_selector(".seam .grab, .divider")
        box = await grab.bounding_box()
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        await page.mouse.move(cx, cy)
        await page.mouse.down()
        await page.mouse.move(cx, cy - 120, steps=8)
        await page.mouse.up()

        # 2. pan the top feed right 80px and zoom in twice
        wrap = await page.query_selector(".pane .media-wrap")
        wbox = await wrap.bounding_box()
        wx, wy = wbox["x"] + wbox["width"] / 2, wbox["y"] + wbox["height"] / 2
        await page.mouse.move(wx, wy)
        await page.mouse.down()
        await page.mouse.move(wx + 80, wy + 20, steps=6)
        await page.mouse.up()
        await page.mouse.wheel(0, -240)
        await page.wait_for_timeout(500)

        ratios = await page.evaluate(
            """() => {
                const panes = [...document.querySelectorAll('.stacked > .pane, .split > .pane')];
                const media = document.querySelector('.pane video, .pane img.snap');
                return {
                    heights: panes.map(p => Math.round(p.offsetHeight)),
                    transform: media && media.style.transform,
                };
            }"""
        )
        print(ratios)
        await page.screenshot(path=str(DEBUG / "ui_adjust.png"))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
