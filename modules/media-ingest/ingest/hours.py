"""Pragmatic OSM `opening_hours` evaluator.

Handles the common Seattle patterns: "24/7", "Mo-Fr 09:00-17:00",
"Mo-Sa 10:00-22:00; Su 11:00-18:00", day lists ("Sa,Su"), multiple time
ranges ("09:00-12:00,13:00-17:00"), overnight spans ("22:00-02:00"),
"off"/"closed" rules, and bare day rules ("Mo-Su" = open those days).

HONESTY RULE: anything we can't parse returns None ("hours unknown"),
never False — an unreadable tag is not a closed business. PH/SH (public
and school holiday) rules are skipped, which means holiday closures are
not modeled; the basis string carried by callers says "osm-opening-hours"
so consumers know exactly what this is.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SEATTLE_TZ = ZoneInfo("America/Los_Angeles")
DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
_DAY_IDX = {d: i for i, d in enumerate(DAYS)}

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_RANGE_RE = re.compile(r"^(\d{1,2}:\d{2})-(\d{1,2}:\d{2})$")


def _minutes(tok: str) -> int | None:
    m = _TIME_RE.match(tok)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 24 or mi > 59:
        return None
    return h * 60 + mi


def _parse_days(tok: str) -> set[int] | None:
    """'Mo-Fr' / 'Sa,Su' / 'Mo' -> set of weekday indices; None if unparseable."""
    out: set[int] = set()
    for part in tok.split(","):
        part = part.strip()
        if "-" in part:
            a, _, b = part.partition("-")
            if a not in _DAY_IDX or b not in _DAY_IDX:
                return None
            i, j = _DAY_IDX[a], _DAY_IDX[b]
            k = i
            while True:                      # ranges wrap: Sa-Mo
                out.add(k)
                if k == j:
                    break
                k = (k + 1) % 7
        else:
            if part not in _DAY_IDX:
                return None
            out.add(_DAY_IDX[part])
    return out


def _parse_rule(rule: str):
    """-> (days: set[int], ranges: list[(start_min, end_min)], closed: bool)
    or None if this rule is unparseable."""
    rule = rule.strip()
    if not rule:
        return None
    if rule == "24/7":
        return set(range(7)), [(0, 24 * 60)], False
    toks = rule.split()
    if toks[0].startswith(("PH", "SH")):
        return "skip"                        # holidays not modeled
    days = _parse_days(toks[0])
    rest = toks[1:]
    if days is None:
        # time-only rule ("09:00-17:00") applies to all days
        days = set(range(7))
        rest = toks
    if not rest:
        return days, [(0, 24 * 60)], False   # bare day rule = open
    if rest[0].lower() in ("off", "closed"):
        return days, [], True
    ranges = []
    for span in " ".join(rest).split(","):
        m = _RANGE_RE.match(span.strip())
        if not m:
            return None
        a, b = _minutes(m.group(1)), _minutes(m.group(2))
        if a is None or b is None:
            return None
        ranges.append((a, b))
    return days, ranges, False


def evaluate(spec: str, at: datetime | None = None):
    """-> (open_now: bool | None, open_until: 'HH:MM' | None).
    None means the spec couldn't be parsed — unknown, not closed."""
    if not spec or not isinstance(spec, str):
        return None, None
    now = (at or datetime.now(SEATTLE_TZ)).astimezone(SEATTLE_TZ)
    day, minute = now.weekday(), now.hour * 60 + now.minute

    rules = []
    for raw in spec.split(";"):
        parsed = _parse_rule(raw)
        if parsed == "skip":
            continue
        if parsed is None:
            return None, None                # any unreadable part -> unknown
        rules.append(parsed)
    if not rules:
        return None, None

    open_now, open_until = False, None
    for days, ranges, closed in rules:
        # later rules override earlier ones for the days they name (OSM semantics)
        if day in days:
            if closed:
                open_now, open_until = False, None
                continue
            open_now, open_until = False, None
            for a, b in ranges:
                if b > a and a <= minute < b:                       # same-day span
                    open_now, open_until = True, f"{b // 60 % 24:02d}:{b % 60:02d}"
                elif b <= a and minute >= a:                        # overnight, before midnight
                    open_now, open_until = True, f"{b // 60 % 24:02d}:{b % 60:02d}"
        # overnight spill-over from yesterday's rule (e.g. 22:00-02:00)
        if (day - 1) % 7 in days and not closed:
            for a, b in ranges:
                if b <= a and minute < b:
                    open_now, open_until = True, f"{b // 60 % 24:02d}:{b % 60:02d}"
    return open_now, open_until


if __name__ == "__main__":
    from datetime import datetime as dt
    fri_2130 = dt(2026, 8, 14, 21, 30, tzinfo=SEATTLE_TZ)   # Friday night
    tue_0300 = dt(2026, 8, 11, 3, 0, tzinfo=SEATTLE_TZ)     # Tuesday 3 am
    cases = [
        ("24/7", fri_2130, True),
        ("Mo-Fr 09:00-17:00", fri_2130, False),
        ("Mo-Sa 10:00-22:00; Su 11:00-18:00", fri_2130, True),
        ("Fr,Sa 17:00-02:00", fri_2130, True),
        ("Fr,Sa 17:00-02:00", tue_0300, False),              # Sa night spills to Su 2am only
        ("Sa 17:00-02:00", dt(2026, 8, 16, 1, 0, tzinfo=SEATTLE_TZ), True),  # Su 1am, Sa overnight
        ("Mo-Su", fri_2130, True),
        ("Mo-Fr 09:00-12:00,13:00-17:00", fri_2130, False),
        ("sunrise-sunset", fri_2130, None),                  # unparseable -> unknown
        ("Mo-Fr 09:00-17:00; Fr off", fri_2130, False),      # later rule wins
    ]
    ok = True
    for spec, when, want in cases:
        got, until = evaluate(spec, when)
        mark = "ok " if got == want else "FAIL"
        ok &= got == want
        print(f"{mark} {spec!r:<45} @{when:%a %H:%M} -> {got} until {until}")
    raise SystemExit(0 if ok else 1)
