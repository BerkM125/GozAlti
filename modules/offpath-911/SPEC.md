# modules/offpath-911 — off-path detection + confirmation-gated escalation

**Owner:** Berkan · **Effort budget:** 2 h

If the user visibly leaves the route we computed for them and stays off it,
ask (via `modules/audio-lm`) whether they're okay, and — only on explicit
confirmation — escalate.

## Mechanism (deterministic, no ML)

- Input: the active `Route` polyline (from `modules/pathfinding`) + live
  device positions.
- **Off-path** = perpendicular distance from the polyline > `OFFPATH_M`
  (default 40 m, GPS urban-canyon noise is real — tune on a real walk) for
  more than `OFFPATH_T` (default 45 s) while moving. Both knobs env-tunable.
- Debounce re-prompts (`REPROMPT_COOLDOWN_S`, default 300) — one unanswered
  prompt must not machine-gun the user.
- State machine: `on_path → drifting(t) → prompted → (ok | escalate | no_answer)`.
  `no_answer` after 60 s → re-prompt once, then surface a persistent visual
  banner; it does NOT auto-escalate.

## Escalation — HARD SAFETY RULES (binding, non-negotiable)

1. **No real 911 calls, ever, in dev or demo.** All escalation targets a
   SIMULATED endpoint (local log + on-screen banner) or, at most, a designated
   teammate number via `modules/calling`. Dialing actual emergency services
   from a hackathon demo is a misuse of emergency infrastructure and is
   forbidden in this codebase.
2. Escalation fires ONLY on the user's explicit spoken/tapped confirmation —
   never on silence, never on classifier confidence alone.
3. What gets sent is evidence-linked only: last GPS fix + timestamp, the
   active route, and camera-derived facts WITH their basis. Nothing
   improvised, nothing inferred-but-stated-as-fact.
4. A visible, always-available CANCEL aborts at any stage.

## iOS reality check (from `modules/ios-pwa`)

No background geolocation in a PWA: this feature works with the screen on.
Demo it that way; do not promise "works with phone in pocket, screen off".

## Definition of done

Simulated walk (GPS trace replay is fine) drifts off a computed route →
prompt fires within `OFFPATH_T`+10 s → spoken "yes" → simulated-escalation
log entry with the exact evidence payload; spoken "no" → state resets;
silence → banner, no escalation. All three paths shown.
