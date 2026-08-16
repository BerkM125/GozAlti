#!/usr/bin/env python3
"""Live status page for whatever this module is currently chewing on.

Stdlib only, no build step, no CDN — the venue wifi dies and this still works.
Reads whatever artifacts exist on disk right now and renders them; nothing is
precomputed, so it is always current.

  ./status.py                      # serve on 0.0.0.0:8090
  ./status.py --port 8091 --once   # print the HTML and exit (for debugging)

Then from anywhere on the LAN or tailnet:
  http://gn100-3511.local:8090      http://100.106.143.38:8090

Shows: GPU + served models, running jobs, per-model VLM records with latency,
detector runs, verdict-guard status, and the newest annotated overlays.
"""
import argparse, glob, html, json, os, re, subprocess, time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent


def sh(cmd, timeout=6):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=timeout).stdout.strip()
    except Exception:
        return ""


def jsonl(path):
    p = HERE / path
    if not p.exists(): return []
    out = []
    for line in p.read_text(errors="replace").splitlines():
        if line.strip():
            try: out.append(json.loads(line))
            except Exception: pass
    return out


def gpu():
    raw = sh("nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu "
             "--format=csv,noheader 2>/dev/null")
    return raw or "nvidia-smi unavailable"


def served_models():
    rows = []
    for port, name in ((8000, "vLLM"),):
        r = sh(f"curl -s -m 3 localhost:{port}/v1/models")
        try:
            for m in json.loads(r)["data"]:
                rows.append((f"{name} :{port}", m["id"], "up"))
        except Exception:
            rows.append((f"{name} :{port}", "—", "down"))
    r = sh("curl -s -m 3 localhost:11434/api/ps")
    try:
        for m in json.loads(r).get("models", []):
            gb = m.get("size", 0) / 1e9
            rows.append(("ollama :11434", m["name"], f"resident {gb:.1f} GB"))
    except Exception:
        pass
    return rows


def jobs():
    out = []
    for pat, label in (("insight.py", "VLM insight"), ("detect.py", "detector"),
                       ("video.py", "video pipeline"), ("bench_detectors", "detector bench"),
                       ("ffmpeg", "clip pull")):
        n = sh(f"pgrep -fc {pat} 2>/dev/null") or "0"
        if n.isdigit() and int(n) > 0:
            out.append((label, f"{n} process(es)"))
    return out


BANNED = re.compile(r"\b(safe|safely|unsafe|danger\w*|risk\w*|suspicious|loiter\w*)\b", re.I)


def verdict_scan(rows):
    bad = 0
    for r in rows:
        txt = " ".join(str(r.get(k) or "") for k in
                       ("activity", "walkway_reason", "setting_notes", "notable", "summary"))
        if BANNED.search(txt): bad += 1
    return bad


def overlays(limit=12):
    pats = ["detect_out/*.jpg", "scene_out/*.jpg", "video_out/*/frames/*.jpg",
            "bench/*/overlays*/*.jpg", "out/*.jpg"]
    files = []
    for p in pats:
        files += glob.glob(str(HERE / p))
    files.sort(key=lambda f: -os.path.getmtime(f))
    return files[:limit]


CSS = """
:root{--bg:#0d1016;--card:#151a22;--line:#232a35;--ink:#d8dee9;--dim:#7d8796;
--go:#3ddc84;--warn:#f2a93b;--bad:#e8443a;--mono:ui-monospace,'SF Mono',Menlo,monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,sans-serif;padding:18px 22px}
h1{font-size:16px;letter-spacing:.14em;text-transform:uppercase}
h1 span{color:var(--go)}
h2{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--dim);
margin:22px 0 8px;border-bottom:1px solid var(--line);padding-bottom:5px}
.top{display:flex;align-items:baseline;gap:16px;flex-wrap:wrap}
.stamp{font:11px var(--mono);color:var(--dim)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:4px;padding:10px 12px}
.card b{display:block;font:19px var(--mono);color:var(--go);font-variant-numeric:tabular-nums}
.card span{font:10px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--dim)}
table{width:100%;border-collapse:collapse;font:12px var(--mono)}
th{text-align:left;font-size:10px;letter-spacing:.08em;text-transform:uppercase;
color:var(--dim);font-weight:600;padding:5px 8px;border-bottom:1px solid var(--line)}
td{padding:5px 8px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:0}
.go{color:var(--go)}.warn{color:var(--warn)}.bad{color:var(--bad)}.dim{color:var(--dim)}
.strip{display:flex;gap:8px;overflow-x:auto;padding-bottom:6px}
.strip figure{flex:0 0 260px}
.strip img{width:100%;border:1px solid var(--line);border-radius:3px;display:block}
.strip figcaption{font:10px var(--mono);color:var(--dim);margin-top:4px;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.empty{color:var(--dim);font:12px var(--mono);padding:8px 0}
pre{font:11px var(--mono);color:var(--ink);white-space:pre-wrap;margin-top:4px}
"""


def render():
    now = datetime.now().strftime("%H:%M:%S")
    p = [f"<!doctype html><meta charset=utf-8><title>vlm status</title>",
         "<meta http-equiv=refresh content=10>", f"<style>{CSS}</style>",
         f"<div class=top><h1>GozAlti <span>/ vlm</span></h1>"
         f"<span class=stamp>{html.escape(gpu())} &middot; {now} &middot; refresh 10s</span></div>"]

    # jobs
    js = jobs()
    p.append("<h2>running now</h2>")
    if js:
        p.append("<table><tr><th>job</th><th>state</th></tr>")
        for label, state in js:
            p.append(f"<tr><td class=go>{html.escape(label)}</td><td>{html.escape(state)}</td></tr>")
        p.append("</table>")
    else:
        p.append("<div class=empty>idle — nothing processing</div>")

    # models
    p.append("<h2>models served</h2><table><tr><th>endpoint</th><th>model</th><th>state</th></tr>")
    for ep, m, st in served_models():
        cls = "go" if st in ("up",) or st.startswith("resident") else "bad"
        p.append(f"<tr><td>{html.escape(ep)}</td><td>{html.escape(m)}</td>"
                 f"<td class={cls}>{html.escape(st)}</td></tr>")
    p.append("</table>")

    # vlm runs
    p.append("<h2>vlm records</h2>")
    files = sorted(glob.glob(str(HERE / "insight*.jsonl"))) + sorted(glob.glob(str(HERE / "video_out/*/*.jsonl")))
    if files:
        p.append("<table><tr><th>file</th><th>model</th><th>records</th><th>mean s</th>"
                 "<th>flags</th><th>verdict guard</th></tr>")
        for f in files:
            rows = jsonl(Path(f).relative_to(HERE))
            if not rows: continue
            secs = [r.get("vlm_seconds") or 0 for r in rows]
            mean = sum(secs) / len(secs) if secs else 0
            fl = {}
            for r in rows:
                for x in (r.get("flags") or []): fl[x] = fl.get(x, 0) + 1
            bad = verdict_scan(rows)
            vc = (f"<span class=go>clean</span>" if not bad
                  else f"<span class=bad>{bad}/{len(rows)} verdict</span>")
            top = ", ".join(f"{k}&times;{v}" for k, v in sorted(fl.items(), key=lambda x: -x[1])[:4])
            p.append(f"<tr><td>{html.escape(Path(f).name)}</td>"
                     f"<td>{html.escape(str(rows[0].get('model','?')))}</td>"
                     f"<td>{len(rows)}</td><td>{mean:.2f}</td>"
                     f"<td class=dim>{top or '—'}</td><td>{vc}</td></tr>")
        p.append("</table>")
    else:
        p.append("<div class=empty>no vlm records yet</div>")

    # detector runs
    det = jsonl("detect.jsonl")
    if det:
        ms = [r.get("ms", 0) for r in det]
        p.append("<h2>detector</h2><div class=grid>")
        for label, val in (("frames", len(det)),
                           ("ms/frame", f"{sum(ms)/len(ms):.0f}"),
                           ("people", sum(r.get("people_count") or 0 for r in det)),
                           ("vehicles", sum(r.get("vehicle_count") or 0 for r in det)),
                           ("646-cam sweep", f"{sum(ms)/len(ms)*646/1000:.0f}s")):
            p.append(f"<div class=card><b>{val}</b><span>{label}</span></div>")
        p.append("</div>")

    # newest overlays
    ov = overlays()
    p.append("<h2>latest frames</h2>")
    if ov:
        p.append("<div class=strip>")
        for f in ov:
            rel = os.path.relpath(f, HERE)
            age = int(time.time() - os.path.getmtime(f))
            p.append(f"<figure><img src='/f/{html.escape(rel)}' loading=lazy>"
                     f"<figcaption>{html.escape(Path(f).name[:44])} &middot; {age}s ago</figcaption></figure>")
        p.append("</div>")
    else:
        p.append("<div class=empty>no overlays on disk yet</div>")

    return "".join(p)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/f/"):
            target = (HERE / path[3:]).resolve()
            if HERE in target.parents and target.exists():
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(target.read_bytes())
                return
            self.send_error(404); return
        body = render().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    if a.once:
        print(render()); return
    srv = ThreadingHTTPServer(("0.0.0.0", a.port), H)
    print(f"vlm status -> http://0.0.0.0:{a.port}  (LAN: gn100-3511.local, tailnet: 100.106.143.38)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
