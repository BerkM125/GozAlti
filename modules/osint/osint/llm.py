"""Local ollama scoring with structured output (ADR-002/003).

Adapted from modules/vlm/lab/ask.py (stream off, keep_alive, temperature 0, audit jsonl),
text-only via /api/chat, plus the retry-on-schema-miss loop the repo never had:
`format` carries a full JSON Schema (ollama constrains decoding to it); a salvage
parser (ported from safe-walk vision._parse) rescues near-misses; pydantic validates;
final failure drops the item — a score is never fabricated."""

import json
import re
import time

import httpx

from . import config
from .models import RawItem, ScoreResult

SCHEMA = {
    "type": "object",
    "properties": {
        "relevant": {"type": "boolean"},
        "sentiment": {"type": "number", "minimum": -1, "maximum": 1},
        "summary": {"type": "string", "maxLength": 200},
    },
    "required": ["relevant", "sentiment", "summary"],
}

SYSTEM = """You score street-level personal-safety perception in Seattle from public posts.
Return JSON: {"relevant": bool, "sentiment": number in [-1,1], "summary": string}.
relevant=true ONLY if the text describes safety, danger, crime, or comfort in a specific
public place (street, intersection, park, neighborhood). Discussions of policy, politics,
housing prices, or events elsewhere are relevant=false.
sentiment: -1 = the place felt or was dangerous, +1 = felt or was safe, 0 = neutral/mixed.
summary: one concrete line a stranger can verify, e.g. "post describing harassment near
3rd & Bell last week" — state what happened and where, never vague vibes.
Never name, quote the handle of, or physically describe any identifiable individual;
describe places and events only."""

_JSON_RE = re.compile(r"\{.*\}", re.S)
_cache: dict[str, dict] | None = None


def _salvage(text: str) -> dict:
    """Ported from safe-walk vision._parse: extract JSON, repair trailing commas, else sentinel."""
    match = _JSON_RE.search(text)
    if not match:
        return {"parse_error": text[:200]}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        repaired = re.sub(r",\s*([}\]])", r"\1", match.group(0))
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            return {"parse_error": text[:200]}


def _load_cache() -> dict[str, dict]:
    global _cache
    if _cache is None:
        _cache = {}
        if config.SCORED_CACHE.exists():
            for line in config.SCORED_CACHE.read_text().splitlines():
                try:
                    rec = json.loads(line)
                    _cache[rec["item_id"]] = rec["result"]
                except (json.JSONDecodeError, KeyError):
                    continue
    return _cache


def score(item: RawItem, area_names: list[str]) -> ScoreResult | None:
    cache = _load_cache()
    if item.id in cache:
        return ScoreResult(**cache[item.id]) if cache[item.id] else None

    user = (
        f"Source: {item.source}. Mentioned area(s): {', '.join(area_names)}.\n"
        f"Title: {item.title}\n\n{item.text[:2000]}"
    )
    result: ScoreResult | None = None
    for attempt in range(1, config.LLM_MAX_ATTEMPTS + 1):
        prompt = user if attempt == 1 else user + "\n\nReturn ONLY JSON matching the schema."
        body = {
            "model": config.MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "keep_alive": "24h",
            "format": SCHEMA,
            "options": {"temperature": 0, "num_predict": 300},
        }
        t0 = time.time()
        try:
            resp = httpx.post(f"{config.OLLAMA_URL}/api/chat", json=body, timeout=600)
            resp.raise_for_status()
            text = resp.json().get("message", {}).get("content", "")
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            _audit(item.id, attempt, time.time() - t0, f"transport error: {exc!r}")
            continue
        _audit(item.id, attempt, time.time() - t0, text)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = _salvage(text)
        if "parse_error" in parsed:
            continue
        try:
            result = ScoreResult(**parsed)
            break
        except Exception:
            continue

    cache[item.id] = result.model_dump() if result else None
    with config.SCORED_CACHE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"item_id": item.id, "result": cache[item.id]}) + "\n")
    return result


def _audit(item_id: str, attempt: int, seconds: float, raw: str) -> None:
    with config.LLM_LOG.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "item_id": item_id,
                    "model": config.MODEL,
                    "attempt": attempt,
                    "seconds": round(seconds, 2),
                    "raw_response": raw[:2000],
                }
            )
            + "\n"
        )
