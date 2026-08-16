# modules/calling — get ahold of a nominated contact

`./run.sh` → **:8060**. Stdlib only, no `pip install`. Runs on the Acer box.

```
POST /alert  {"contact":"Dhruv","to":"+1555...","message":"..."}
GET  /health          which channels are armed, and whether any of them RINGS
GET  /                browser test page
```

## What can actually ring a phone, free

We looked at the obvious candidates. Most of them cannot do this at all:

| | rings a phone? | why |
|---|---|---|
| **Google Voice** | ❌ | No public API, and never had one. `pygooglevoice` and friends were reverse-engineered, broke years ago, and violate ToS. The Workspace Voice API provisions users; it does not place calls. |
| **Telegram bots** | ❌ | Bot API is messages only. Telegram's 1:1 calls are peer-to-peer between user accounts with no API. A userbot can join a *group voice chat* and stream audio, but the contact has to already be in it. |
| **Discord bots** | ❌ | A bot can join a voice channel the contact is already sitting in. It cannot ring anyone. |
| **WhatsApp** | ⚠️ | The Business Calling API is real, but gated behind Meta business verification — days, not hours. |
| **CallMeBot** | ✅ | Rings the contact's **Telegram** and speaks the message. Free, no account, one HTTP GET. |
| **Twilio** | ✅ | Rings the actual **cellular number**. Free trial credit; a trial account may only dial numbers you have verified. |

So there are exactly two free paths to a ringing phone, and this module implements both.

## Setup — CallMeBot, ~2 minutes, no account

1. **The contact** opens Telegram and sends `/start` to **@CallMeBot_txtbot**. That is the
   authorisation step; nobody can be called who has not opted in.
2. `cp .env.example .env`, set `CALLMEBOT_USER=@their_username`.
3. `./run.sh`, then `curl -X POST localhost:8060/alert -H 'Content-Type: application/json' \
   -d '{"contact":"Dhruv","message":"Test from Safe Walk."}'`

Three limits worth knowing before you demo on it, one of which is undocumented: the spoken text is capped at **256
characters** (we truncate deliberately rather than let it cut mid-word), and Telegram's
**iOS app has a bug where call audio does not play**. The ring still lands, and `cc=yes`
delivers the same text as a chat message, so an iPhone contact gets the buzz and the
words but not the voice. Do not make it the only armed channel for an iPhone contact.

The undocumented one: **two calls to the same user within 65 seconds are refused.** Their
API returns HTTP 200 with the refusal in the page body, so budget a minute between demo
takes and do not stack a rehearsal immediately before the real run.

CallMeBot signals every outcome — success, unauthorised recipient, rate limit — as HTTP
200 with prose in an HTML page. `service.py` parses that prose and only reports `sent` on
a positive acknowledgement. The `provider_said` field in every `/alert` response carries
their exact sentence, so a call that does not arrive can be diagnosed rather than guessed
at.

## Setup — Twilio, ~10 minutes, real cellular ring

Console → verify the contact's number → copy SID/token/from-number into `.env`. The call
plays `<Say>` twice, because people miss the first sentence of a robocall.

## Why fan-out instead of one channel

The requirement is not "place a call", it is *somebody has to get ahold*. A single channel
has a single failure mode: phone on silent, bot muted, trial credit gone, venue wifi
captive-portalled. So `/alert` fires every armed channel at once and reports each
outcome separately. Partial success is normal and the response says which parts worked.

The response distinguishes **tried** from **reached** — `reached:false` returns HTTP 502.
The caller just told a person "I'm contacting someone"; it needs to know whether that was
true, not merely whether we attempted it.

## Safety

Emergency numbers are refused at the door in every channel, with an HTTP 403 and a
stderr log, regardless of what the caller sends — see `BLOCKED_NUMBERS`. This enforces
`modules/offpath-911` binding rule 1 in code rather than by convention. This service does
not decide to escalate; it is told to, by a flow that already took two explicit
confirmations and can be cancelled at any point.
