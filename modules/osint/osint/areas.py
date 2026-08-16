"""Hand-set neighborhood registry (ADR-004). Centroids seeded from safe-walk api.PLACES
and hand-checked; SPD's `neighborhood` field carries MCPP names (live vocabulary verified
2026-08-15 via $select=distinct neighborhood), matched exactly — no fuzzy geocoding."""

import math
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Area:
    slug: str
    name: str
    lat: float
    lon: float
    aliases: list[str] = field(default_factory=list)
    mcpp: list[str] = field(default_factory=list)


AREAS = [
    Area("belltown", "Belltown", 47.6141, -122.3459,
         ["belltown", "3rd and bell", "third and bell", "bell st"],
         ["BELLTOWN"]),
    Area("downtown", "Downtown", 47.6104, -122.3372,
         ["downtown", "westlake", "pike place", "pike st", "pine st", "3rd ave", "third ave"],
         ["DOWNTOWN COMMERCIAL"]),
    Area("pioneer-square", "Pioneer Square", 47.6015, -122.3343,
         ["pioneer square"],
         ["PIONEER SQUARE"]),
    Area("international-district", "Chinatown-International District", 47.5983, -122.3255,
         ["international district", "chinatown", "little saigon", "c-id"],
         ["CHINATOWN/INTERNATIONAL DISTRICT"]),
    Area("capitol-hill", "Capitol Hill", 47.6140, -122.3205,
         ["capitol hill", "cap hill", "broadway"],
         ["CAPITOL HILL", "MILLER PARK"]),
    Area("first-hill", "First Hill", 47.6085, -122.3235,
         ["first hill"],
         ["FIRST HILL"]),
    Area("south-lake-union", "South Lake Union", 47.6255, -122.3370,
         ["south lake union", "slu", "denny triangle"],
         ["SLU/CASCADE"]),
    Area("lower-queen-anne", "Lower Queen Anne / Uptown", 47.6236, -122.3552,
         ["lower queen anne", "uptown", "seattle center"],
         []),  # SPD has one QUEEN ANNE mcpp; it maps to queen-anne
    Area("queen-anne", "Queen Anne", 47.6323, -122.3565,
         ["queen anne"],
         ["QUEEN ANNE"]),
    Area("u-district", "University District", 47.6608, -122.3130,
         ["u district", "u-district", "university district", "university way", "the ave"],
         ["UNIVERSITY"]),
    Area("central-district", "Central District", 47.6076, -122.3028,
         ["central district", "squire park"],
         ["CENTRAL AREA/SQUIRE PARK"]),
    Area("ballard", "Ballard", 47.6685, -122.3840,
         ["ballard"],
         ["BALLARD NORTH", "BALLARD SOUTH"]),
    Area("fremont", "Fremont", 47.6510, -122.3500,
         ["fremont"],
         ["FREMONT"]),
    Area("sodo", "SoDo", 47.5810, -122.3320,
         ["sodo", "stadium district"],
         ["SODO"]),
]

BY_SLUG = {a.slug: a for a in AREAS}
_MCPP = {m: a.slug for a in AREAS for m in a.mcpp}
_ALIAS_RE = [
    (a.slug, re.compile(r"\b" + re.escape(alias) + r"\b", re.IGNORECASE))
    for a in AREAS
    for alias in a.aliases
]

MATCH_POINT_MAX_KM = 1.2


def match_text(text: str, cap: int = 2) -> list[str]:
    """Alias keyword matching over free text; at most `cap` areas per item."""
    hits: list[str] = []
    for slug, rx in _ALIAS_RE:
        if slug not in hits and rx.search(text):
            hits.append(slug)
        if len(hits) >= cap:
            break
    return hits


def match_mcpp(mcpp: str | None) -> str | None:
    return _MCPP.get((mcpp or "").strip().upper())


def match_point(lat: float, lon: float) -> str | None:
    """Nearest centroid within MATCH_POINT_MAX_KM; SPD blurs coords to the hundred-block,
    which is noise at neighborhood granularity."""
    best, best_km = None, MATCH_POINT_MAX_KM
    for a in AREAS:
        km = _haversine_km(lat, lon, a.lat, a.lon)
        if km < best_km:
            best, best_km = a.slug, km
    return best


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))
