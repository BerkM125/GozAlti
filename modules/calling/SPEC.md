# modules/calling — SPEC (see README.md for the built service)

**Owner:** unassigned · **Effort budget:** 1 h · **Cut first if time runs out.**

Place a real phone call to a **designated, consenting contact** (e.g. a
teammate's number) and read them an evidence-backed status: the user's
location and what nearby cameras actually observed.

## Scope, if anyone picks it up

- Twilio (or similar) programmable-voice call to ONE pre-configured number
  from `.env` (`CALLING_CONTACT_NUMBER`) — never user-entered at runtime,
  never scraped, **never an emergency number** (that rule lives in
  `modules/offpath-911` and applies here too).
- The spoken payload is assembled from real data only: last GPS fix, active
  route segment, and camera-derived facts with their basis (e.g. "the nearest
  camera, CMR-0257 at 4th & Pine, last showed pixel activity 2 minutes ago").
  If a datum is unknown it is omitted, not invented.
- Triggered only from `modules/offpath-911`'s confirmation-gated flow or an
  explicit in-app button. Same CANCEL rule.
- The contact must have consented beforehand (it's a teammate for the demo).

## Honest assessment

The original story said "might be too much" — correct. This is a Twilio
account, a webhook, TTS-to-call plumbing, and a live phone network dependency
on demo day, for a feature the simulated-escalation banner already
demonstrates. Build it only if everything in §5.1's order of attack is done
and demo recording hasn't started.
