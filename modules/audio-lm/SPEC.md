# modules/audio-lm — voice companion (STT + TTS conversation loop)

**Owner:** Adi · **Effort budget:** 3 h

Open-weight speech loop on the Spark: hear the user, talk back, run short
confirmation dialogues. Consumed by `modules/offpath-911` (it supplies the
prompts; this module supplies ears + voice).

## Scope

- **STT**: Whisper-class open-weight model (faster-whisper / whisper.cpp are
  both fine on the Spark; pick one, measure latency, write it here).
- **TTS**: any local open-weight voice (Piper is the boring safe choice).
- **Dialogue**: short, state-machine-driven exchanges — NOT open-ended chat.
  The canonical flow:

      AI:   "Hey, I noticed you went off path. Everything good?"
      User: "No."
      AI:   "Should I contact emergency services with your exact location?"
      User: "Yes."
      AI:   "Contacting…"   → hands off to offpath-911's escalation hook

- Intent detection for yes/no/help keywords from the transcript; anything
  ambiguous → re-ask, never assume.
- Mic capture in the PWA needs a **secure context** (see `modules/ios-pwa`) —
  same HTTPS fix as geolocation, test both at once.

## Binding rules

- **Confirmation-gated**: this module never initiates any outbound contact by
  itself. It reports the user's confirmed intent to its caller; escalation
  policy (and its hard safety rules) lives in `modules/offpath-911`.
- **No fabricated situation reports**: anything the voice says about the
  user's surroundings must come from real, evidence-linked data (location,
  camera-derived facts with their basis) — never improvised color.
- Transcripts + synthesized replies logged locally (gitignored) for the demo
  debrief.
- Wake-word/keyword handling must fail SAFE: unrecognized audio → re-prompt,
  never → escalation.

## Definition of done

Round-trip demo on the Spark: spoken "no" → escalation question → spoken
"yes" → escalation hook fires (into offpath-911's SIMULATED endpoint), full
loop under ~3 s per turn, tested with the actual demo phone's mic through the
PWA.
