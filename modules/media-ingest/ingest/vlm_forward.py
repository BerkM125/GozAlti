"""Hot-lane push to the vlm module (POST :8040/read) with temporal
breadcrumbs, forwarding the returned Observation to synthesis.

Request body shape (agreed sibling-key layout — FrameRecord §6.1 untouched):

    {
      "frame_record": { ...FrameRecord §6.1 verbatim... },
      "image_b64": "<jpeg>",
      "prior_observations": [ ...last 2-3 Observations §6.2, verbatim... ]
    }

CONTRACT NOTE (spec injection, binding): `prior_observations` is NOT part of
§6.1 — it travels as a sibling key precisely so the contract stays untouched.
Adi/Dhruv must confirm :8040/read tolerates/uses it; if it graduates into the
contract, that's a god-spec §6 edit with owner sign-off, never a silent one.

Gated on VLM_READ_URL; unset = this path is off and nothing is sent.
"""
from __future__ import annotations

import base64
import threading

import httpx

from . import config, feeds, observations

_client: httpx.Client | None = None
_client_lock = threading.Lock()


def enabled() -> bool:
    return bool(config.VLM_READ_URL)


def _cli() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(timeout=config.VLM_TIMEOUT)
        return _client


def read_camera(node: dict) -> dict | None:
    """Fetch the freshest frame for this node (rate-gated), push it to
    :8040/read with breadcrumbs, record + forward the Observation.
    Returns the Observation or None."""
    if not enabled():
        return None
    cid = node["camera_id"]
    blob, rec = feeds.latest_frame(node, prefer="hls")
    if blob is None or rec is None or rec.get("stale"):
        return None
    body = {
        "frame_record": rec,
        "image_b64": base64.standard_b64encode(blob).decode(),
        "prior_observations": observations.priors(cid),
    }
    try:
        r = _cli().post(config.VLM_READ_URL, json=body)
        r.raise_for_status()
        obs = r.json()
    except Exception:
        return None
    if not isinstance(obs, dict) or "camera_id" not in obs:
        return None   # not an Observation-shaped reply; don't store junk
    observations.record(cid, obs)
    _forward_to_synthesis(obs)
    return obs


def _forward_to_synthesis(obs: dict) -> None:
    if not config.SYNTH_OBS_URL:
        return
    try:
        _cli().post(config.SYNTH_OBS_URL, json=obs)
    except Exception:
        pass   # synthesis being down must not break the hot lane
