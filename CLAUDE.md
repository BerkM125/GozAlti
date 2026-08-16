# CLAUDE.md — session rules for GozAlti

You are working inside **GozAlti**, a 5-person NVIDIA Spark Hack (See track)
monorepo with a hard deadline of **Sunday Aug 16, 4:00 PM PDT**. These rules are
binding for every Claude session in this repo.

**FINAL-NIGHT CLOCK: `SPEC.md` §5.2 sets hour-by-hour deadlines (1:00 AM →
4:00 AM submission, Sunday Aug 16 PDT), binding on every dev and every agent
session. Check the current time against that table before starting work; slots
whose deadline has passed are frozen — bug fixes only, no new scope.**

## Before touching anything

1. Read `SPEC.md` (the god spec) in full — mission, demo definition of done,
   module registry, data contracts.
2. Ask the user which module they own if they haven't said. Then read that
   module's `modules/<name>/SPEC.md`.
3. Read the specific `experiments/` files your module spec tells you to harvest
   before writing new code.

## Hard rules

- **Lane discipline**: edit only inside the user's module directory (plus
  `demo/`). If the task requires changing another module or a contract in
  `SPEC.md` §6, STOP and surface it to the user — never change another module's
  code or a shared contract silently.
- **`experiments/` is read-only.** Copy code out into the module and adapt the
  copy. Never edit, delete, or "clean up" anything under `experiments/`.
- **Contracts are law**: produce/consume exactly the JSON shapes in `SPEC.md` §6,
  validated at boundaries. A schema mismatch is a bug to fix, not a shape to bend.
- **No invented safety verdicts**: the VLM describes what it sees; only synthesis
  combines evidence, and every user-facing claim carries its evidence. Never
  fabricate data, numbers, or model output — not even placeholders that look real.
- **Rate limits**: ≥60 s per camera between snapshot fetches, ≤4 concurrent
  upstream requests, descriptive User-Agent. No exceptions, including "just for
  testing."
- **Secrets** via `.env` (gitignored) only. Heavy/rebuildable data stays
  gitignored (`experiments/surukamera/data/` is the shipped exception).
- **Don't break `main`**: run what you changed before calling it done; park
  broken work on a `mod/<module>` branch. Do not commit or push unless the user
  asks.

## Working style

- Demo-readiness beats feature count. When in doubt, do the thing that makes the
  Sunday demo (walk + route + camera click + live warning) more reliable.
- Keep the module's quickstart in its SPEC.md current with every change.
- Prefer porting proven experiment code over novel implementations; the
  experiments have already survived contact with SDOT's real endpoints.
- Small, focused diffs with commit messages that name the contract(s) touched.
