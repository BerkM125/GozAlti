#!/usr/bin/env python3
"""modules/calling — get ahold of a nominated contact. Port 8060.

  POST /alert   {"contact":"Dhruv","message":"...","to":"+1555..."} -> per-channel results
  GET  /health                                                     -> which channels are armed
  GET  /                                                           -> browser test page

WHY FAN-OUT AND NOT ONE CHANNEL
-------------------------------
The requirement is not "place a call", it is "somebody has to get ahold". Those are
different problems. A single channel has a single failure mode: the phone is on silent,
the bot is muted, the trial credit ran out, the venue wifi is captive-portalled. So we
fire every armed channel at once and report each one's outcome separately. Partial
success is the normal case and the response says exactly which parts worked.

WHAT ACTUALLY RINGS A PHONE
---------------------------
Only telephony. Telegram and Discord bots cannot initiate a call to a person — Telegram's
calls are peer-to-peer between user accounts with no API, and Discord bots can only join
a voice channel the person is already in. WhatsApp's Business Calling API exists but
needs Meta business verification, which is measured in days.

Two channels here actually ring. `twilio` rings the cellular number (trial credit, and a
trial account may only dial numbers it has verified — a teammate qualifies). `callmebot`
rings the contact's Telegram with a spoken message and needs no account at all, at the
cost of a 256-character limit and a Telegram-on-iOS bug that mutes call audio. The rest
are loud notifications, which is a real and useful thing to be, just not the same thing.

CONFIGURATION — all optional, arm what you have
-----------------------------------------------
  CALLMEBOT_USER                         @username or +phone; RINGS Telegram, free
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   from @BotFather, ~2 minutes, free
  NTFY_TOPIC                             no account at all, https://ntfy.sh
  DISCORD_WEBHOOK_URL                    channel settings -> integrations
  TWILIO_SID + TWILIO_TOKEN              account SID and auth token, OR
  TWILIO_SID + TWILIO_API_KEY/SECRET     account SID and an API key pair
  TWILIO_FROM                            a number the account owns; trial credit buys one
  CALLING_CONTACT_NUMBER                 default destination when the caller sends no "to"

SAFETY
------
Emergency numbers are refused at the door, in every channel, regardless of what the
caller sends. modules/offpath-911's rule ("no real 911 calls, ever, in dev or demo")
applies here and is enforced in code rather than by convention — see BLOCKED_NUMBERS.
This service does not decide to escalate; it is told to, by a flow that already took two
explicit confirmations.
"""
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("CALLING_PORT", "8060"))
# Twilio trial accounts refuse free-text SMS bodies (error 572006, "predefined templates
# only") while placing calls perfectly well. Rather than ship a channel that always fails
# and clutters every response, texting is opt-in and turns on once the account is upgraded.
SMS_ENABLED = os.environ.get("TWILIO_SMS", "").lower() in ("1", "true", "yes")
TIMEOUT = 10

# Refused in every channel. Digits only, after stripping punctuation. This list is
# deliberately broad: a false refusal costs a demo, a false dial costs a real dispatch.
BLOCKED_NUMBERS = {
    "911", "1911", "999", "112", "000", "110", "119", "118", "115",
    "988",            # US suicide & crisis lifeline
    "18002738255",    # ditto, long form
}


def blocked(number: str) -> bool:
    d = re.sub(r"\D", "", number or "")
    if not d:
        return False
    # Match the bare emergency number, and the same with a leading country code.
    return d in BLOCKED_NUMBERS or any(
        d.endswith(b) and len(d) - len(b) <= 2 for b in BLOCKED_NUMBERS
    )


def visible_text(html: str) -> str:
    """The human-readable text of a response, with script and style blocks removed.

    Providers that answer in HTML bury their status message under a page of analytics
    and markup. Naively stripping tags surfaces the Google Analytics snippet instead of
    the sentence we care about, which is exactly what happened the first time.
    """
    t = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", html or "")
    t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&")
    return " ".join(t.split())


def post(url, data=None, headers=None, method="POST"):
    """One HTTP call, returning (ok, detail, body). Never raises — a dead channel must
    not take down the other channels.

    The body comes back even on success because not every provider signals failure with
    a status code. CallMeBot in particular answers 200 with an error page when the
    recipient has not authorised the bot, so a channel that trusted the status alone
    would report a call that never happened."""
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read()[:20000].decode("utf-8", "replace")
            return True, f"{r.status}", body
    except urllib.error.HTTPError as e:
        body = e.read()[:200].decode("utf-8", "replace")
        return False, f"HTTP {e.code}: {body}", body
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", ""


# ---------------------------------------------------------------- channels

def send_telegram(msg, **_):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return None
    body = urllib.parse.urlencode({
        "chat_id": chat, "text": msg, "disable_web_page_preview": "false",
    }).encode()
    return post(f"https://api.telegram.org/bot{token}/sendMessage", body,
                {"Content-Type": "application/x-www-form-urlencoded"})


def send_ntfy(msg, contact="", **_):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return None
    # Priority 5 is ntfy's "urgent": it rings through a silenced phone on Android and
    # shows a critical alert on iOS. This is the closest a free, account-less channel
    # gets to a phone call.
    return post(f"https://ntfy.sh/{topic}", msg.encode("utf-8"), {
        "Title": f"Safe Walk alert for {contact}"[:120],
        "Priority": "5",
        "Tags": "rotating_light",
    })


def send_discord(msg, **_):
    hook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not hook:
        return None
    return post(hook, json.dumps({"content": msg[:1900]}).encode(),
                {"Content-Type": "application/json"})


def twilio_auth():
    """Twilio accepts two credential pairs, and the difference trips people up.

      * Account SID + Auth Token — the pair on the console home page.
      * API Key SID (SK...) + its Secret — revocable, and the better practice, but the
        REST path still addresses the ACCOUNT, so the Account SID is required either way.

    Returns (account_sid, basic_auth_user, basic_auth_password) or None.
    """
    acct = os.environ.get("TWILIO_SID") or os.environ.get("TWILIO_ACCOUNT_SID") or ""
    key = os.environ.get("TWILIO_API_KEY") or os.environ.get("TWILIO_KEY_SID") or ""
    secret = os.environ.get("TWILIO_API_SECRET") or os.environ.get("TWILIO_CLIENT_SECRET") or ""
    token = os.environ.get("TWILIO_TOKEN") or os.environ.get("TWILIO_AUTH_TOKEN") or ""
    if not acct:
        return None
    if key and secret:
        return acct, key, secret
    if token:
        return acct, acct, token
    return None


def send_twilio(msg, to="", **_):
    creds = twilio_auth()
    frm = os.environ.get("TWILIO_FROM")
    if not (creds and frm and to):
        return None
    sid, user, pw = creds
    # Trial accounts are refused the inline `Twiml` parameter — "trial accounts have
    # limited parameter access". So the call points at a hosted TwiML `Url` instead.
    # Twimlets is Twilio's own free hosted endpoint, which means no tunnel and no public
    # server of our own just to read one sentence aloud. Set TWILIO_TWIML_URL to override
    # with your own TwiML Bin or endpoint.
    safe = msg.replace("&", "and").replace("<", "").replace(">", "")
    url = os.environ.get("TWILIO_TWIML_URL")
    if not url:
        # Message[0] twice: people miss the first sentence of a robocall.
        url = "https://twimlets.com/message?" + urllib.parse.urlencode(
            [("Message[0]", safe), ("Message[1]", safe)])
    body = urllib.parse.urlencode({"To": to, "From": frm, "Url": url}).encode()
    auth = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json", body, {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    })



def classify_callmebot(text: str):
    """Decide what CallMeBot's HTML page actually says. Pure, so it can be tested against
    captured responses without ringing anyone — see test_callmebot.py.

    Everything they return is HTTP 200, including refusals, so this function is the only
    thing standing between a failed call and the app telling someone "help is coming".
    It is deliberately anchored on exact phrases rather than loose keywords: an earlier
    version searched the whole page for words like "ok" and "error" and misread a
    successful call as a failure, because a marketing page contains both.

    Note "Autorization OK" — their typo, not ours. Matching only the correct spelling
    would fail every successful call.
    """
    t = " ".join((text or "").split()).lower()
    if not t:
        return False, "empty response"
    if "is not received" in t or "not authorized" in t:
        return False, "recipient has not authorised — send /start to @CallMeBot_txtbot"
    if "within 65 seconds is not allowed" in t:
        return False, "rate limited: 65 s between calls to the same user"
    if t.startswith("error:") or " error: " in t:
        i = t.find("error:")
        return False, t[i:i + 160]
    if "autorization ok" in t or "authorization ok" in t:
        return True, "authorised, text handed to TTS"
    if "queued" in t or "call in progress" in t:
        return True, "queued"
    # Neither a recognised success nor a recognised failure. Report the uncertainty
    # rather than guessing in either direction.
    return False, f"unrecognised response: {t[:160]}"


def send_callmebot(msg, **_):
    """Rings the contact's Telegram and reads the message aloud. Free, no account, no
    credit card — the recipient authorises once by sending /start to @CallMeBot_txtbot.

    Three caveats that matter and are not all documented:
      * 256 character hard limit on the spoken text, so we truncate deliberately rather
        than let the service silently cut mid-sentence.
      * Two calls to the same user within 65 seconds are refused. Budget a minute
        between demo takes.
      * Telegram's iOS app has a known bug where call audio does not play. The ring still
        arrives, which is most of the value, and `cc=yes` sends the same text as a chat
        message so the content survives even when the audio does not. Do not rely on this
        channel alone for an iPhone contact.
    """
    user = os.environ.get("CALLMEBOT_USER")
    if not user:
        return None
    spoken = msg if len(msg) <= 250 else msg[:247] + "..."
    q = urllib.parse.urlencode({
        "user": user, "text": spoken, "lang": "en-US-Standard-C",
        "rpt": "2",     # say it twice; people miss the first sentence of a robocall
        "cc": "yes",    # carbon-copy as chat text, our insurance against the iOS bug
    })
    ok, detail, body = post(f"https://api.callmebot.com/start.php?{q}", None, {}, method="GET")
    if not ok:
        return ok, detail, body
    good, why = classify_callmebot(visible_text(body))
    return good, why, body


def send_twilio_sms(msg, to="", sms=None, **_):
    """Text the contact from the same Twilio number that called them.

    Separate from the voice channel on purpose. A spoken message cannot carry a link,
    and a call that is missed leaves nothing behind; a text is the durable half of the
    same alert. The caller may supply a different body for the text than for the call —
    that is what `sms` is for — because a map URL read aloud is noise, and an address
    the voice promised needs to actually arrive somewhere.
    """
    creds = twilio_auth()
    frm = os.environ.get("TWILIO_FROM")
    if not (creds and frm and to):
        return None
    sid, user, pw = creds
    body = urllib.parse.urlencode({"To": to, "From": frm, "Body": (sms or msg)[:1500]}).encode()
    auth = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", body, {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    })


CHANNELS = {
    "twilio": send_twilio,        # rings a real cellular phone
    "callmebot": send_callmebot,  # rings Telegram, free, no account
    "telegram": send_telegram,
    "ntfy": send_ntfy,
    "discord": send_discord,
}


def armed():
    """Which channels have enough configuration to be worth trying."""
    return {
        "callmebot": bool(os.environ.get("CALLMEBOT_USER")),
        "twilio": bool(twilio_auth() and os.environ.get("TWILIO_FROM")),
        "twilio_sms": bool(SMS_ENABLED and twilio_auth() and os.environ.get("TWILIO_FROM")),
        "telegram": bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")),
        "ntfy": bool(os.environ.get("NTFY_TOPIC")),
        "discord": bool(os.environ.get("DISCORD_WEBHOOK_URL")),
    }


# Recent alerts, newest last. In memory on purpose: this is evidence for a demo and a
# debugging aid, not a record we want to persist about someone's movements.
ALERTS = []
ALERTS_MAX = 50


def remember(entry):
    ALERTS.append(entry)
    del ALERTS[:-ALERTS_MAX]
    return entry


def fan_out(message, contact, to, sms=None):
    """Text first, then call, and let the call describe what actually happened.

    The spoken message promises "sending you their address by text now". Making that
    promise before knowing whether the text went out means the voice can lie — and on a
    trial account it reliably does, because free-text SMS is refused (572006) while the
    call succeeds. So the SMS is attempted first and the promise is appended to the
    spoken text only once it is true. A contact who is told an address is coming will
    wait for it; a contact who is told there is none will look at the call log instead.
    """
    results, reached = {}, False
    if sms and SMS_ENABLED:
        out = send_twilio_sms(message, contact=contact, to=to, sms=sms)
        if out is None:
            results["twilio_sms"] = {"status": "not configured"}
        else:
            ok, detail, body = out
            results["twilio_sms"] = {"status": "sent" if ok else "failed", "detail": detail}
            reached = reached or ok
            if ok:
                message = message.rstrip() + " Sending you their address by text now."

    for name, fn in CHANNELS.items():
        if name in results:
            continue
        out = fn(message, contact=contact, to=to, sms=sms)
        if out is None:
            results[name] = {"status": "not configured"}
            continue
        ok, detail, body = out
        # Carry what the provider actually said, not just our verdict on it. When a demo
        # alert does not arrive, this line is the difference between debugging and
        # guessing.
        said = visible_text(body)[:200]
        results[name] = {"status": "sent" if ok else "failed", "detail": detail}
        if said:
            results[name]["provider_said"] = said
        reached = reached or ok
    return reached, results


# ---------------------------------------------------------------- http

PAGE = """<!doctype html><meta charset=utf-8><title>calling</title>
<style>body{font:14px ui-monospace,monospace;background:#0d0f14;color:#e6e9ef;padding:24px;max-width:640px}
input,textarea,button{font:inherit;width:100%;margin:4px 0;padding:8px;background:#161a22;color:#e6e9ef;border:1px solid #2a2f3a;border-radius:6px}
button{background:#3ddb85;color:#000;font-weight:600;cursor:pointer}pre{white-space:pre-wrap;color:#8b93a3}</style>
<h3>modules/calling — fan-out test</h3>
<input id=contact value=Dhruv><input id=to placeholder="+1555..."><textarea id=msg rows=4>Test alert from GozAlti Safe Walk.</textarea>
<button onclick=go()>send</button><pre id=out></pre>
<script>async function go(){const r=await fetch('/alert',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({contact:contact.value,to:to.value,message:msg.value})});out.textContent=JSON.stringify(await r.json(),null,2)}</script>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            self._send(200, {"ok": True, "port": PORT, "channels": armed(),
                             "rings_a_phone": armed()["twilio"] or armed()["callmebot"]})
        elif path == "/alerts":
            # Newest first, which is what a dashboard wants. Trim the message body so a
            # poll is cheap; /alerts/<id> has the whole thing.
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            limit = int((q.get("limit") or ["20"])[0])
            rows = [{k: (v[:120] if k == "message" else v) for k, v in a.items()}
                    for a in reversed(ALERTS[-limit:])]
            self._send(200, {"count": len(ALERTS), "alerts": rows})
        elif path.startswith("/alerts/"):
            wanted = path.rsplit("/", 1)[-1]
            hit = next((a for a in ALERTS if a["id"] == wanted), None)
            self._send(200 if hit else 404, hit or {"error": f"no alert {wanted}"})
        elif path == "/":
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/alert":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._send(400, {"error": f"bad JSON: {e}"})

        message = (payload.get("message") or "").strip()
        if not message:
            return self._send(400, {"error": "message is required"})
        contact = (payload.get("contact") or "your contact").strip()
        configured = (os.environ.get("CALLING_CONTACT_NUMBER") or "").strip()
        to = (payload.get("to") or "").strip()
        # A caller-supplied number that cannot be a phone number is worse than no number
        # at all: it fails the whole alert instead of falling back. The phone's contact
        # field is free text and may hold anything a thumb left there.
        if to and len(re.sub(r"\D", "", to)) < 10:
            sys.stderr.write(f"[calling] ignoring unusable to={to!r}, using configured contact\n")
            to = ""
        to = to or configured

        if blocked(to):
            # Loud refusal, logged, 403. This is the one thing in this file that must
            # never be softened into a warning.
            sys.stderr.write(f"REFUSED emergency destination: {to!r}\n")
            return self._send(403, {
                "error": "refused: this service never contacts emergency services",
                "rule": "modules/offpath-911 binding rule 1",
            })

        # Log what came in and what each channel said. A failure that reaches the phone
        # as a bare status code is a failure you debug by guessing.
        sys.stderr.write(f"[calling] alert contact={contact!r} to={to!r} "
                         f"msg_len={len(message)} sms={'yes' if payload.get('sms') else 'no'}\n")
        started = time.time()
        reached, results = fan_out(message, contact, to,
                                   sms=(payload.get("sms") or "").strip() or None)
        entry = remember({
            "id": f"alert-{len(ALERTS) + 1:04d}",
            "at": started,
            "contact": contact,
            "to": to,
            "message": message,
            "reached": reached,
            "channels": results,
            "took_s": round(time.time() - started, 2),
        })
        for name, r in results.items():
            if r["status"] != "not configured":
                sys.stderr.write(f"[calling]   {name}: {r['status']} — {r.get('detail')}\n")
        self._send(200 if reached else 502, {
            "id": entry["id"],
            "reached": reached,
            "channels": results,
            # The caller told a person "I'm calling someone" — it needs to know whether
            # that was true, not just whether we tried.
            "note": ("at least one channel accepted the alert" if reached
                     else "NO channel accepted the alert — the user was not reached"),
        })

    def log_message(self, fmt, *args):
        sys.stderr.write("[calling] " + fmt % args + "\n")


if __name__ == "__main__":
    a = armed()
    print(f"[calling] :{PORT}  armed={[k for k, v in a.items() if v] or 'NONE'}", file=sys.stderr)
    if not (a["twilio"] or a["callmebot"]):
        print("[calling] nothing armed that RINGS — set CALLMEBOT_USER (free) or TWILIO_*",
              file=sys.stderr)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
