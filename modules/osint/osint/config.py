"""All knobs in one place. Every tunable is env-overridable; .env in the module root wins."""

import os
from pathlib import Path

from dotenv import load_dotenv

MODULE_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(MODULE_ROOT / ".env")

DATA = MODULE_ROOT / "data"
RAW = DATA / "raw"
CACHE = DATA / "cache"
SIGNALS_JSONL = DATA / "signals.jsonl"        # contract output (SPEC.md §6.3), append-only
SIGNALS_LATEST = DATA / "signals.latest.json"  # deduped snapshot; synthesis should prefer this
AREA_WEIGHTS = DATA / "area_weights.json"      # internal artifact, NOT a §6 contract
LLM_LOG = DATA / "llm_log.jsonl"               # audit log of every ollama call
SCORED_CACHE = CACHE / "scored.jsonl"          # item_id -> LLM result; reruns cost zero LLM calls

for _d in (RAW / "spd", RAW / "reddit", RAW / "news", CACHE):
    _d.mkdir(parents=True, exist_ok=True)

USER_AGENT = os.getenv(
    "OSINT_USER_AGENT",
    "GozAlti-osint/0.1 (Seattle hackathon research; contact a_dhruv@outlook.com)",
)

# LLM (local ollama on the Spark)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.getenv("OSINT_MODEL", "nemotron-cascade-2:30b")
LLM_MAX_ATTEMPTS = int(os.getenv("OSINT_LLM_ATTEMPTS", "3"))

# Pacing: gentle sequential fetching. Concurrency is 1 by design (well under the ≤4 cap).
PACING_S = {"spd": 1.0, "reddit": 2.0, "news": 1.0}

# SPD Crime Data (SODA, data.seattle.gov). Structured — no scraping, no LLM.
SPD_DATASET = "tazs-3rd5"
SPD_BASE = f"https://data.seattle.gov/resource/{SPD_DATASET}.json"
SPD_WINDOW_DAYS = int(os.getenv("OSINT_SPD_WINDOW_DAYS", "30"))
# Street-relevant violent offenses only (ADR-007). Verified against the live
# distinct offense_sub_category vocabulary on 2026-08-15.
SPD_OFFENSES = [
    "AGGRAVATED ASSAULT",
    "ASSAULT OFFENSES",
    "ROBBERY",
    "WEAPON LAW VIOLATION",
    "HOMICIDE",
]

# Reddit public JSON API
REDDIT_SUBS = ["Seattle", "SeattleWA"]
REDDIT_QUERY = (
    'safety OR unsafe OR sketchy OR robbed OR assault OR "broke into" OR shooting OR harassed'
)

# Local news RSS (summaries only; article-text fetch is the optional Playwright path).
# All three verified live on 2026-08-15; mynorthwest.com 403s bots and was dropped.
NEWS_FEEDS = [
    "https://www.seattletimes.com/seattle-news/feed/",
    "https://www.capitolhillseattle.com/feed/",
    "https://westseattleblog.com/feed/",
]

# Keyword gate: an item must hit one of these before it costs an LLM call.
SAFETY_LEXICON = [
    "safety", "unsafe", "safe", "sketchy", "dangerous", "danger", "robbed", "robbery",
    "assault", "assaulted", "attacked", "shooting", "shot", "stabbed", "stabbing",
    "harass", "harassed", "harassment", "followed", "stolen", "theft", "broke into",
    "break-in", "mugged", "crime", "avoid", "scary", "threatened", "gunfire",
]

# Aggregate weights (internal): exponential decay + per-source influence cap,
# mirroring safe-walk's LIVE_CAP idea — one loud source can't own a neighborhood.
HALF_LIFE_DAYS = float(os.getenv("OSINT_HALF_LIFE_DAYS", "14"))
SOURCE_CAP = float(os.getenv("OSINT_SOURCE_CAP", "0.35"))
