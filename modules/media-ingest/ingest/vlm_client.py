"""Thin VLM access for this module's two internal needs:

  1. orientation precompute — frame vs annotated satellite reconciliation
  2. the fast-lane object sweep — lightweight "what is in this FOV" reads

Primary backend: any OpenAI-compatible endpoint (the Spark's NIM / VLM WebUI
/ the vlm module on :8040) via VLM_BASE_URL. Fallback: Anthropic vision if
ANTHROPIC_API_KEY is set. If neither is configured every call returns None —
nodes then simply carry no detections; nothing is ever fabricated.

This is NOT the safety-analysis path — that is modules/vlm (Adi). This lane
only produces descriptive object/context reads (SPEC §1: the VLM describes,
synthesis decides).
"""
from __future__ import annotations

import base64
import json
import re
from functools import lru_cache

import httpx

from . import config, netboot

DETECTION_LABELS = ["person", "car", "truck", "bus", "bicycle", "motorcycle",
                    "dog", "construction", "debris", "crowd", "other"]

DETECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "detections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "enum": DETECTION_LABELS},
                    "cx": {"type": "number", "minimum": 0, "maximum": 1},
                    "cy": {"type": "number", "minimum": 0, "maximum": 1},
                    "conf": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["label", "cx", "cy", "conf"],
                "additionalProperties": False,
            },
        },
        "caption": {"type": "string"},
    },
    "required": ["detections", "caption"],
    "additionalProperties": False,
}

SATELLITE_SCHEMA = {
    "type": "object",
    "properties": {
        "matching_arrow": {"type": "string", "enum": ["A", "B", "neither", "unclear"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["matching_arrow", "confidence"],
    "additionalProperties": False,
}

DETECTION_PROMPT = (
    "This is a public traffic-camera frame from Seattle. List clearly visible "
    "objects of these types only: " + ", ".join(DETECTION_LABELS) + ". For each, "
    "give its center as normalized image coordinates cx, cy in [0,1] from the "
    "top-left, and a confidence in [0,1]. Then one factual sentence describing "
    "the scene (weather, lighting, sidewalk/road state). Report only what is "
    "actually visible; an empty list is a valid answer. No judgments about "
    "danger or safety — description only."
)

SATELLITE_PROMPT = (
    "Image 1 is a Seattle traffic-camera frame; image 2 is a satellite view of "
    "the same location with two arrows, A and B, marking the two possible "
    "viewing directions of the camera along the road axis. Compare buildings, "
    "road layout, and surroundings: which arrow direction matches what the "
    "camera sees? Judge from road structure and building positions only."
)


def _b64(jpeg: bytes) -> str:
    return base64.standard_b64encode(jpeg).decode()


def _parse_json(text: str) -> dict | None:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:text.rfind("}") + 1])
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------- backends

@lru_cache(maxsize=1)
def _openai_client() -> httpx.Client | None:
    if not config.VLM_BASE_URL:
        return None
    return httpx.Client(
        base_url=config.VLM_BASE_URL.rstrip("/"),
        headers={"Authorization": f"Bearer {config.VLM_API_KEY}"},
        timeout=config.VLM_TIMEOUT,
    )


@lru_cache(maxsize=1)
def _anthropic_client():
    if not config.ANTHROPIC_KEY:
        return None
    try:
        import anthropic
        from anthropic import DefaultHttpxClient
    except ImportError:
        return None
    return anthropic.Anthropic(
        api_key=config.ANTHROPIC_KEY,
        http_client=DefaultHttpxClient(proxy=netboot.ensure_proxy()),
    )


def available() -> bool:
    return _openai_client() is not None or _anthropic_client() is not None


def backend() -> str | None:
    if _openai_client() is not None:
        return f"openai-compatible:{config.VLM_MODEL}"
    if _anthropic_client() is not None:
        return "anthropic:claude-haiku-4-5-20251001"
    return None


def _query_openai(prompt: str, images: list[bytes], schema: dict) -> dict | None:
    cli = _openai_client()
    if cli is None:
        return None
    content: list[dict] = [
        {"type": "image_url",
         "image_url": {"url": f"data:image/jpeg;base64,{_b64(img)}"}}
        for img in images
    ]
    content.append({"type": "text", "text":
                    prompt + "\nAnswer with ONLY a JSON object matching this "
                    "schema, no prose:\n" + json.dumps(schema)})
    try:
        r = cli.post("/chat/completions", json={
            "model": config.VLM_MODEL,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 1024,
            "temperature": 0,
        })
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        return _parse_json(text)
    except Exception:
        return None


def _query_anthropic(prompt: str, images: list[bytes], schema: dict) -> dict | None:
    cli = _anthropic_client()
    if cli is None:
        return None
    blocks = [{"type": "image",
               "source": {"type": "base64", "media_type": "image/jpeg",
                          "data": _b64(img)}} for img in images]
    blocks.append({"type": "text", "text": prompt})
    try:
        resp = cli.messages.create(
            model="claude-haiku-4-5-20251001",   # lightweight fast lane
            max_tokens=1024,
            output_config={"format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": blocks}],
        )
        if resp.stop_reason == "refusal":
            return None
        text = next((b.text for b in resp.content if b.type == "text"), None)
        return json.loads(text) if text else None
    except Exception:
        return None


def query_json(prompt: str, images: list[bytes], schema: dict) -> dict | None:
    out = _query_openai(prompt, images, schema)
    if out is None:
        out = _query_anthropic(prompt, images, schema)
    return out


# ------------------------------------------------------------ typed queries

def detect_objects(frame_jpeg: bytes) -> dict | None:
    """{"detections": [{label, cx, cy, conf}], "caption": str} or None."""
    out = query_json(DETECTION_PROMPT, [frame_jpeg], DETECTION_SCHEMA)
    if not out or not isinstance(out.get("detections"), list):
        return None
    clean = []
    for d in out["detections"]:
        try:
            if d["label"] in DETECTION_LABELS:
                clean.append({"label": d["label"],
                              "cx": max(0.0, min(1.0, float(d["cx"]))),
                              "cy": max(0.0, min(1.0, float(d["cy"]))),
                              "conf": max(0.0, min(1.0, float(d["conf"])))})
        except (KeyError, TypeError, ValueError):
            continue
    return {"detections": clean, "caption": str(out.get("caption", ""))[:300]}


def satellite_cross_check(frame_jpeg: bytes, satellite_png: bytes,
                          dir_a: float, dir_b: float) -> dict | None:
    """Frame + annotated satellite -> which axis hypothesis the camera faces.
    Returns {"bearing_deg", "arrow", "confidence"} or None."""
    out = query_json(SATELLITE_PROMPT, [frame_jpeg, satellite_png], SATELLITE_SCHEMA)
    if not out or out.get("matching_arrow") in (None, "neither", "unclear"):
        return None
    bearing = dir_a if out["matching_arrow"] == "A" else dir_b
    return {"bearing_deg": round(bearing % 360.0, 1),
            "arrow": out["matching_arrow"],
            "confidence": out.get("confidence", "low")}
