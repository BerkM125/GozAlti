"""Per-camera ring buffer of Observations (SPEC §6.2) — the temporal
breadcrumbs replayed to the VLM so it can report *change*.

Observations are stored VERBATIM, original timestamps intact — no
summarizing, no aggregation beyond selection: the moment we paraphrase
model output we're manufacturing evidence. Mirrored to
data/observations/<camera_id>.jsonl for restart resilience.
"""
from __future__ import annotations

import json
import threading
from collections import deque

from . import config

_LOCK = threading.Lock()
_BUF: dict[str, deque] = {}   # camera_id -> deque of Observation dicts


def _path(camera_id: str):
    return config.OBSERVATIONS_DIR / f"{camera_id}.jsonl"


def record(camera_id: str, observation: dict) -> None:
    """Store an Observation that round-tripped from the VLM, verbatim."""
    with _LOCK:
        buf = _BUF.setdefault(camera_id, deque(maxlen=config.PRIOR_OBSERVATIONS_N))
        buf.append(observation)
        with _path(camera_id).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(observation) + "\n")


def priors(camera_id: str) -> list[dict]:
    """The last N Observations for this camera, oldest first."""
    with _LOCK:
        buf = _BUF.get(camera_id)
        if buf is None:
            buf = deque(maxlen=config.PRIOR_OBSERVATIONS_N)
            p = _path(camera_id)
            if p.exists():
                lines = p.read_text(encoding="utf-8").splitlines()
                for line in lines[-config.PRIOR_OBSERVATIONS_N:]:
                    try:
                        buf.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            _BUF[camera_id] = buf
        return list(buf)
