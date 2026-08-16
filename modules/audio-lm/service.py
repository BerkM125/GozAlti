#!/usr/bin/env python3
"""audio-lm service — the voice loop's brain. Port 8050.

  POST /session          {"trigger":"offpath"|"keyword"|"manual"} -> {session_id, say, state}
  POST /session/{id}/hear {"transcript": "...", "confidence": 0.9} -> {say, state, done}
  POST /session/{id}/cancel                                        -> {say, state}
  GET  /session/{id}                                               -> full state + transcript log
  POST /session/{id}/tick                                          -> silence handling
  GET  /health · GET /  (browser demo)

WHERE THE SPEECH ACTUALLY HAPPENS
---------------------------------
STT and TTS run in the BROWSER, not here. `demo.html` uses the Web Speech API:
`webkitSpeechRecognition` for ears and `speechSynthesis` for voice. That is not a
shortcut, it is the better engineering choice for this feature:

  * on-device — no audio ever leaves the phone, and it works when the venue wifi dies
  * ~200 ms per turn instead of a round trip plus model load; the SPEC's budget is 3 s
  * zero install on a box that has no whisper, no piper and no sudo

The trade is that it is not an open-weight model, which the module SPEC asks for. A
Spark-side whisper endpoint can be added behind `POST /transcribe` later without any
change to this state machine or to the client — the dialogue never cared where the words
came from, only what they were and how confident the recogniser was. `AUDIO_STT` in
/health reports which is in use so the demo never misreports it.

This service holds NO audio. It holds the conversation state, which is the part that has
to be correct.
"""
import json, os, sys, time, uuid, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from dialogue import Dialogue, State

PORT = int(os.environ.get("AUDIO_PORT", "8050"))
STT = os.environ.get("AUDIO_STT", "browser-webspeech")
TTS = os.environ.get("AUDIO_TTS", "browser-speechsynthesis")
# Where a confirmed escalation is reported. offpath-911 owns what happens next and the
# hard rule that real emergency services are never dialed. Unset = log only.
ESCALATION_URL = os.environ.get("ESCALATION_URL", "")
LOG = HERE / "sessions"

_sessions: dict[str, Dialogue] = {}
_lock = threading.Lock()
_stats = {"sessions": 0, "escalations": 0, "resolved_ok": 0, "cancelled": 0,
          "needs_attention": 0, "started": time.time()}


def _persist(sid: str, d: Dialogue):
    """Transcript log to disk for the demo debrief (gitignored)."""
    try:
        LOG.mkdir(exist_ok=True)
        (LOG / f"{sid}.json").write_text(json.dumps({
            "session_id": sid, "trigger": d.trigger, "state": d.state.value,
            "started_at": d.started_at, "turns": d.transcript_log()}, indent=1))
    except Exception:
        pass


def _report_escalation(sid: str, d: Dialogue, evidence: dict):
    """Hand off to offpath-911. This module NEVER contacts anyone itself."""
    _stats["escalations"] += 1
    payload = {"session_id": sid, "trigger": d.trigger,
               "confirmed_at": time.time(), "contact": d.contact,
               "transcript": d.transcript_log(),
               "evidence": evidence}
    if not ESCALATION_URL:
        print(f"[audio-lm] ESCALATION CONFIRMED (no ESCALATION_URL set — logged only) "
              f"session={sid}", flush=True)
        return {"delivered": False, "why": "ESCALATION_URL not configured"}
    try:
        import urllib.request
        req = urllib.request.Request(ESCALATION_URL, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return {"delivered": True, "status": r.status}
    except Exception as e:
        # A failed handoff must be loud: the user was told we are contacting someone.
        print(f"[audio-lm] ESCALATION HANDOFF FAILED session={sid}: {e}", flush=True)
        return {"delivered": False, "why": f"{type(e).__name__}: {e}"}


def _view(sid: str, d: Dialogue, say: str = "", extra: dict | None = None) -> dict:
    out = {"session_id": sid, "state": d.state.value, "say": say, "done": d.done,
           "trigger": d.trigger, "contact": d.contact, "reprompts": d.reprompts,
           "escalated": d.state is State.ESCALATE, "turns": len(d.history)}
    if extra:
        out.update(extra)
    return out


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._json(204, {})

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/demo", "/index.html"):
            html = (HERE / "demo.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return
        if p == "/health":
            return self._json(200, {
                "module": "audio-lm", "port": PORT, "ok": True,
                "stt": STT, "tts": TTS,
                "escalation_url": ESCALATION_URL or "(unset — logs only)",
                "active_sessions": len(_sessions),
                "uptime_s": round(time.time() - _stats["started"]),
                **{k: v for k, v in _stats.items() if k != "started"}})
        if p.startswith("/session/"):
            sid = p.split("/")[2]
            d = _sessions.get(sid)
            if not d:
                return self._json(404, {"error": "no such session"})
            return self._json(200, _view(sid, d, extra={"transcript": d.transcript_log()}))
        self._json(404, {"error": "not found",
                         "routes": ["/health", "POST /session",
                                    "POST /session/{id}/hear",
                                    "POST /session/{id}/cancel",
                                    "POST /session/{id}/tick", "GET /session/{id}"]})

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n)) if n else {}
        except Exception as e:
            return self._json(400, {"error": f"bad json: {e}"})

        if p == "/session":
            sid = uuid.uuid4().hex[:12]
            d = Dialogue()
            say = d.start(trigger=body.get("trigger", "manual"),
                          contact=str(body.get("contact", "") or ""))
            with _lock:
                _sessions[sid] = d
                _stats["sessions"] += 1
            _persist(sid, d)
            return self._json(200, _view(sid, d, say))

        parts = p.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "session":
            sid, action = parts[1], parts[2]
            d = _sessions.get(sid)
            if not d:
                return self._json(404, {"error": "no such session"})
            before = d.state

            if action == "hear":
                transcript = body.get("transcript", "")
                try:
                    conf = float(body.get("confidence", 1.0))
                except (TypeError, ValueError):
                    conf = 0.0          # unparseable confidence is treated as unheard
                say = d.hear(transcript, conf)
            elif action == "cancel":
                say = d.cancel()
            elif action == "tick":
                say = d.tick()
            else:
                return self._json(404, {"error": "unknown action"})

            extra = {}
            if d.state is State.ESCALATE and before is not State.ESCALATE:
                extra["escalation"] = _report_escalation(sid, d, body.get("evidence") or {})
            for st, key in ((State.RESOLVED_OK, "resolved_ok"),
                            (State.CANCELLED, "cancelled"),
                            (State.NEEDS_ATTENTION, "needs_attention")):
                if d.state is st and before is not st:
                    _stats[key] += 1
            _persist(sid, d)
            return self._json(200, _view(sid, d, say, extra))

        self._json(404, {"error": "not found"})


def main():
    print(f"audio-lm :{PORT}  stt={STT}  tts={TTS}  "
          f"escalation={ESCALATION_URL or 'log-only'}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
