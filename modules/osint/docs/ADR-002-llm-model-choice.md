# ADR-002: LLM model choice

Date: 2026-08-15.
Status: accepted.

## Context

Sentiment scoring in [-1, 1] plus a one-line concrete evidence summary per item, batched over tens of posts, running locally on the GN100 (GB10, ~120 GB unified memory).
Installed candidates: nemotron-cascade-2:30b (ollama, 24 GB), gpt-oss:120b (65 GB), qwen3.5:122b (81 GB), qwen3:32b, and a Nemotron-3-Nano-Omni-30B GGUF not yet registered with ollama.

## Decision

`nemotron-cascade-2:30b` via local ollama (`http://127.0.0.1:11434`, `/api/chat`), temperature 0, `keep_alive 24h`, ollama structured outputs with a full JSON Schema in the `format` field.
Verified 2026-08-15 on a real Seattle Times item: schema-valid JSON on attempt 1, ~31 s per call.
NVIDIA-branded, which is on-theme for the Spark Hack, and comfortably adequate for a 3-field classification/summarization task.
The model name is env-overridable (`OSINT_MODEL`) so swapping costs nothing.

## Consequences

~30 s per scored item bounds throughput; the keyword gate and per-item cache keep call volume low (tens, not hundreds).

## Alternatives rejected

gpt-oss:120b / qwen3.5:122b: higher quality than the task needs, slower, and 65-81 GB resident alongside the VLM module's models.
Cloud endpoints: unnecessary; local inference is the point of the hardware.
