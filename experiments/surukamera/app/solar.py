"""Solar position (NOAA algorithm, simplified SPA).

Given lat/lon and a UTC timestamp, returns the sun's compass azimuth and
elevation. Exact to well under a degree — far tighter than the +/-30 deg
the shadow/glare bearing layer needs.
"""
from __future__ import annotations

import math
import time


def solar_position(lat: float, lon: float, unix_ts: float) -> tuple[float, float]:
    """Return (azimuth_deg clockwise from north, elevation_deg)."""
    jd = unix_ts / 86400.0 + 2440587.5
    T = (jd - 2451545.0) / 36525.0

    L0 = (280.46646 + T * (36000.76983 + 0.0003032 * T)) % 360.0
    M = math.radians(357.52911 + T * (35999.05029 - 0.0001537 * T))
    e = 0.016708634 - T * (0.000042037 + 0.0000001267 * T)

    C = (math.sin(M) * (1.914602 - T * (0.004817 + 0.000014 * T))
         + math.sin(2 * M) * (0.019993 - 0.000101 * T)
         + math.sin(3 * M) * 0.000289)
    true_long = L0 + C
    omega = math.radians(125.04 - 1934.136 * T)
    lam = math.radians(true_long - 0.00569 - 0.00478 * math.sin(omega))

    eps0 = 23.0 + (26.0 + (21.448 - T * (46.8150 + T * (0.00059 - T * 0.001813))) / 60.0) / 60.0
    eps = math.radians(eps0 + 0.00256 * math.cos(omega))

    decl = math.asin(math.sin(eps) * math.sin(lam))

    y = math.tan(eps / 2.0) ** 2
    L0r = math.radians(L0)
    eqtime = 4.0 * math.degrees(
        y * math.sin(2 * L0r) - 2 * e * math.sin(M)
        + 4 * e * y * math.sin(M) * math.cos(2 * L0r)
        - 0.5 * y * y * math.sin(4 * L0r) - 1.25 * e * e * math.sin(2 * M)
    )

    utc_minutes = ((unix_ts % 86400.0) / 60.0)
    tst = (utc_minutes + eqtime + 4.0 * lon) % 1440.0
    ha = math.radians(tst / 4.0 - 180.0 if tst / 4.0 >= 0 else tst / 4.0 + 180.0)
    if tst / 4.0 < 180.0:
        ha = math.radians(tst / 4.0 + 180.0)
    else:
        ha = math.radians(tst / 4.0 - 180.0)

    latr = math.radians(lat)
    cos_zen = (math.sin(latr) * math.sin(decl)
               + math.cos(latr) * math.cos(decl) * math.cos(ha))
    cos_zen = max(-1.0, min(1.0, cos_zen))
    zen = math.acos(cos_zen)
    elevation = 90.0 - math.degrees(zen)

    if abs(math.sin(zen)) < 1e-6:
        return 0.0, elevation
    cos_az = (math.sin(latr) * cos_zen - math.sin(decl)) / (math.cos(latr) * math.sin(zen))
    cos_az = max(-1.0, min(1.0, cos_az))
    az = math.degrees(math.acos(cos_az))
    if ha > 0:
        azimuth = (az + 180.0) % 360.0
    else:
        azimuth = (540.0 - az) % 360.0
    return azimuth, elevation


if __name__ == "__main__":
    # sanity: Seattle now
    az, el = solar_position(47.61, -122.33, time.time())
    print(f"sun azimuth={az:.1f} deg elevation={el:.1f} deg")
