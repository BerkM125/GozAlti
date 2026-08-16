"""Boundary shapes. AreaSignal is the SPEC.md §6.3 contract — validate loudly, never bend."""

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Centroid(BaseModel):
    lat: float
    lon: float


class AreaSignal(BaseModel):
    """SPEC.md §6.3 — osint -> synthesis. Exactly these fields."""

    area: str
    centroid: Centroid
    source: str  # "reddit" | "news" | "spd" | "other"
    url: str
    observed_at: str  # ISO-8601 UTC, source-event time (not scrape time)
    sentiment: float = Field(ge=-1, le=1)
    summary: str

    @field_validator("source")
    @classmethod
    def _source_enum(cls, v: str) -> str:
        if v not in ("reddit", "news", "spd", "other"):
            raise ValueError(f"source {v!r} not in contract enum")
        return v

    @field_validator("observed_at")
    @classmethod
    def _iso(cls, v: str) -> str:
        datetime.fromisoformat(v.replace("Z", "+00:00"))  # raises if malformed
        return v


class RawItem(BaseModel):
    """Intermediate shape between fetch and scoring (text sources only)."""

    id: str  # reddit fullname / sha1 of news link — dedup + scored-cache key
    source: str
    url: str
    title: str
    text: str = ""
    published_at: str  # ISO-8601 UTC, source-event time


class ScoreResult(BaseModel):
    """What the LLM must return, enforced via ollama structured output + retry."""

    relevant: bool
    sentiment: float = Field(ge=-1, le=1)
    summary: str = Field(max_length=200)
