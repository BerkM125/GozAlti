"""Page-text fetchers behind one interface (ADR-003). HTTP-first; Playwright is the
fallback for JS-heavy article pages only and needs the `browser` extra:
    uv sync --extra browser && uv run playwright install chromium"""

from typing import Protocol

from . import net


class Fetcher(Protocol):
    def fetch_text(self, url: str) -> str: ...


class HttpxFetcher:
    def fetch_text(self, url: str) -> str:
        with net.make_client() as client:
            return net.get_with_retry(client, url).text


class PlaywrightFetcher:
    def fetch_text(self, url: str) -> str:
        from playwright.sync_api import sync_playwright  # lazy: optional dependency

        from . import config

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=config.USER_AGENT)
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                return page.inner_text("body")
            finally:
                browser.close()
