"""Screenshot the running app for visual verification."""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
DEBUG = ROOT / "debug"


async def main() -> None:
    street = sys.argv[1] if len(sys.argv) > 1 else None
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1720, "height": 980})
        page.on("console", lambda m: print("console:", m.type, m.text[:200]))
        page.on("pageerror", lambda e: print("pageerror:", str(e)[:300]))
        await page.goto("http://127.0.0.1:8000/", timeout=30000)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=str(DEBUG / "ui_initial.png"))

        # click a street (first in list, or matching arg)
        items = await page.query_selector_all(".street-item")
        target = None
        if street:
            for it in items:
                if street.lower() in (await it.inner_text()).lower():
                    target = it
                    break
        target = target or (items[0] if items else None)
        if target:
            print("clicking street:", (await target.inner_text())[:60])
            await target.click()
            # geometry computation can take a while on cold cameras
            for _ in range(24):
                await page.wait_for_timeout(5000)
                if await page.query_selector(".pane"):
                    break
            await page.wait_for_timeout(6000)  # let video start
            await page.screenshot(path=str(DEBUG / "ui_pair.png"))
        await browser.close()
    print("screenshots written to debug/")


if __name__ == "__main__":
    asyncio.run(main())
