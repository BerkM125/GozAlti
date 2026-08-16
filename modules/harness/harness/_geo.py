"""Shared geo utilities — used by both routing.py and cameras.py."""

import math


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in meters. a/b are (lon, lat)."""
    R = 6371000.0
    p1, p2 = math.radians(a[1]), math.radians(b[1])
    dp = p2 - p1
    dl = math.radians(b[0] - a[0])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))
