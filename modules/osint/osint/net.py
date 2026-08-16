"""HTTP discipline: one client factory (ported from surukamera netboot.make_client,
proxy dropped) and the retry/backoff the experiments never had. No bare httpx.get anywhere."""

import random
import time

import httpx

from . import config


def make_client(**kwargs) -> httpx.Client:
    kwargs.setdefault("headers", {})["User-Agent"] = config.USER_AGENT
    kwargs.setdefault("timeout", 20.0)
    kwargs.setdefault("follow_redirects", True)
    return httpx.Client(**kwargs)


def get_with_retry(
    client: httpx.Client, url: str, params=None, headers=None, attempts: int = 4
) -> httpx.Response:
    """GET with exponential backoff. 429/503 honor Retry-After; 5xx retry; other 4xx raise."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            resp = client.get(url, params=params, headers=headers)
        except httpx.TransportError as exc:
            last = exc
            _sleep(i, None, url, repr(exc))
            continue
        if resp.status_code < 400:
            return resp
        if resp.status_code in (429, 503):
            _sleep(i, resp.headers.get("Retry-After"), url, f"HTTP {resp.status_code}")
            last = httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
            continue
        if resp.status_code >= 500:
            _sleep(i, None, url, f"HTTP {resp.status_code}")
            last = httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
            continue
        resp.raise_for_status()
    raise last if last else RuntimeError(f"unreachable: {url}")


def _sleep(attempt: int, retry_after: str | None, url: str, why: str) -> None:
    if retry_after and retry_after.isdigit():
        delay = float(retry_after)
    else:
        delay = (2**attempt) * 1.5 + random.uniform(0, 0.5)
    print(f"[net] retry {attempt + 1} in {delay:.1f}s ({why}) {url}", flush=True)
    time.sleep(delay)
