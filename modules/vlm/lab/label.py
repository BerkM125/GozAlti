#!/usr/bin/env python3
"""Blind labelling tool: build the ground truth we do not have.

Every accuracy claim in this module is model-vs-model or eyeballed. This produces a
real answer key for the one question the product actually makes a promise about:
**is the pedestrian path obstructed right now.**

Deliberately NOT shown to the labeller: any model's prediction, the Mac's earlier read,
or the sample's filename tag. A labelling tool that shows you the model's answer
measures agreement, not accuracy.

Deliberately NOT labelled: people counts and vehicle counts. Nobody's routing decision
turns on 13 vs 15 people, and labelling them would cost the hour we do not have.

  ./label.py                      # serve on 0.0.0.0:8095
  ./label.py --set eval           # label lab/eval/ instead of lab/samples/
  ./label.py --score              # no server: score every *.jsonl against labels.jsonl

Keys: 1 clear · 2 narrowed · 3 blocked · 4 no sidewalk · 5 can't tell · ← → navigate.
Each keypress saves immediately and advances, so 50 frames is a few minutes.
Labels land in labels.jsonl (committed — it is the answer key, not an artifact).
"""
import argparse, glob, html, json, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HERE = Path(__file__).resolve().parent

# The only axis we label. "no_sidewalk" is kept separate from "blocked" because they are
# different facts: one is a permanent property of the street (and SDOT already publishes
# it), the other is a transient condition only a camera can see. See ../SPEC.md.
CHOICES = [
    ("clear", "1", "a walkable path exists and nothing is on it"),
    ("narrowed", "2", "path exists but something takes up part of it"),
    ("blocked", "3", "path exists but you could not walk through"),
    ("no_sidewalk", "4", "no pedestrian path here at all (ramp, shoulder, freeway)"),
    ("unclear", "5", "cannot tell from this frame — too dark, too far, obscured"),
]
VALID = {c for c, _, _ in CHOICES}


def frames(setname):
    d = HERE / setname
    return sorted(str(p) for p in d.glob("*.jpg"))


def load_labels(path):
    out = {}
    p = HERE / path
    if p.exists():
        for line in p.read_text().splitlines():
            if line.strip():
                try:
                    r = json.loads(line)
                    out[r["frame"]] = r          # later lines win: relabelling is allowed
                except Exception:
                    pass
    return out


PAGE = """<!doctype html><meta charset=utf-8><title>label · walkway</title>
<style>
:root{--bg:#0d1016;--card:#151a22;--line:#232a35;--ink:#d8dee9;--dim:#7d8796;
--go:#3ddc84;--warn:#f2a93b;--bad:#e8443a;--mono:ui-monospace,Menlo,monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font:14px system-ui,sans-serif;padding:14px 18px}
.bar{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:10px}
h1{font-size:13px;letter-spacing:.14em;text-transform:uppercase}
h1 span{color:var(--go)}
.meta{font:11px var(--mono);color:var(--dim)}
.prog{flex:1;height:4px;background:var(--line);border-radius:2px;overflow:hidden;min-width:120px}
.prog i{display:block;height:100%;background:var(--go)}
figure{background:#000;border:1px solid var(--line);border-radius:4px;overflow:hidden}
img{width:100%;max-height:66vh;object-fit:contain;display:block}
.keys{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
button{flex:1 1 150px;background:var(--card);color:var(--ink);border:1px solid var(--line);
border-radius:4px;padding:9px 10px;cursor:pointer;text-align:left;font:inherit}
button:hover{border-color:var(--go)}
button b{display:block;font:13px var(--mono);color:var(--go)}
button.sel{border-color:var(--go);background:#16241c}
button small{color:var(--dim);font-size:10.5px;line-height:1.35;display:block;margin-top:2px}
kbd{font:10px var(--mono);background:var(--line);border-radius:3px;padding:1px 5px;color:var(--dim)}
.nav{display:flex;gap:8px;margin-top:8px;align-items:center;font:11px var(--mono);color:var(--dim)}
.nav button{flex:0 0 auto}
.done{color:var(--go)}
</style>
<div class=bar>
  <h1>label <span>/ walkway</span></h1>
  <span class=meta id=pos></span>
  <span class=prog><i id=bar style="width:0%"></i></span>
  <span class=meta id=count></span>
</div>
<figure><img id=img alt="camera frame"></figure>
<div class=keys id=keys></div>
<div class=nav>
  <button onclick="go(-1)">&larr; prev</button>
  <button onclick="go(1)">next &rarr;</button>
  <span>a keypress saves and advances &middot; revisit any frame to change its label</span>
</div>
<script>
const CH = __CHOICES__;
let F = [], L = {}, i = 0;
async function boot(){
  const r = await (await fetch('/data')).json();
  F = r.frames; L = r.labels; i = F.findIndex(f => !L[f]); if (i < 0) i = 0;
  document.getElementById('keys').innerHTML = CH.map(c =>
    `<button id="b_${c[0]}" onclick="pick('${c[0]}')"><b>${c[0]}</b>
     <small><kbd>${c[1]}</kbd> ${c[2]}</small></button>`).join('');
  draw();
}
function draw(){
  if (!F.length) return;
  const f = F[i];
  document.getElementById('img').src = '/f?p=' + encodeURIComponent(f) + '&t=' + i;
  document.getElementById('pos').textContent = (i+1) + ' / ' + F.length;
  const n = Object.keys(L).length;
  document.getElementById('bar').style.width = (100*n/F.length) + '%';
  document.getElementById('count').innerHTML = n + ' labelled' +
    (n === F.length ? ' <span class=done>&mdash; complete</span>' : '');
  CH.forEach(c => document.getElementById('b_'+c[0]).classList.toggle('sel', L[f] === c[0]));
}
async function pick(v){
  const f = F[i]; L[f] = v;
  fetch('/save', {method:'POST', headers:{'Content-Type':'application/json'},
                  body: JSON.stringify({frame:f, walkway:v})});
  if (i < F.length - 1) i++;
  draw();
}
function go(d){ i = Math.max(0, Math.min(F.length-1, i+d)); draw(); }
addEventListener('keydown', e => {
  if (e.key === 'ArrowRight') return go(1);
  if (e.key === 'ArrowLeft') return go(-1);
  const c = CH.find(c => c[1] === e.key); if (c) pick(c[0]);
});
boot();
</script>"""


# ---------- scoring ---------------------------------------------------------------

def score(labels_path, set_name):
    """Score every prediction file against the answer key. Prints a confusion matrix."""
    truth = {Path(k).name: v["walkway"] for k, v in load_labels(labels_path).items()}
    if not truth:
        sys.exit(f"no labels in {labels_path} — label some frames first")
    preds = sorted(glob.glob(str(HERE / "insight*.jsonl")))
    if not preds:
        sys.exit("no insight*.jsonl prediction files found")
    print(f"answer key: {len(truth)} labelled frames\n")
    for pf in preds:
        rows = {}
        for line in Path(pf).read_text().splitlines():
            if not line.strip(): continue
            try: r = json.loads(line)
            except Exception: continue
            rows[Path(r.get("frame", "")).name] = r.get("walkway_status")
        common = [f for f in truth if f in rows and rows[f]]
        if not common:
            print(f"{Path(pf).name}: no overlap with the answer key"); continue
        model = "?"
        for line in Path(pf).read_text().splitlines():
            if line.strip():
                try: model = json.loads(line).get("model", "?"); break
                except Exception: pass
        exact = sum(1 for f in common if rows[f] == truth[f])
        # the claim that matters: did we say a path was fine when it was not
        missed = [f for f in common if truth[f] in ("blocked", "narrowed", "no_sidewalk")
                  and rows[f] == "clear"]
        false_alarm = [f for f in common if truth[f] == "clear"
                       and rows[f] in ("blocked", "narrowed", "no_sidewalk")]
        print(f"{Path(pf).name}  [{model}]  n={len(common)}")
        print(f"  exact match         {exact}/{len(common)}  ({100*exact/len(common):.0f}%)")
        print(f"  said CLEAR, was not {len(missed)}   <- the dangerous error")
        for f in missed[:5]: print(f"      {f[:52]}  truth={truth[f]}")
        print(f"  false alarms        {len(false_alarm)}")
        for f in false_alarm[:3]: print(f"      {f[:52]}  said={rows[f]}")
        print()


# ---------- server ----------------------------------------------------------------

def make_handler(fs, labels_path):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/data":
                self._send(200, "application/json", json.dumps({
                    "frames": fs,
                    "labels": {k: v["walkway"] for k, v in load_labels(labels_path).items()},
                }).encode()); return
            if u.path == "/f":
                p = Path(parse_qs(u.query).get("p", [""])[0]).resolve()
                if HERE in p.parents and p.exists():
                    self._send(200, "image/jpeg", p.read_bytes())
                else:
                    self.send_error(404)
                return
            page = PAGE.replace("__CHOICES__", json.dumps(CHOICES))
            self._send(200, "text/html; charset=utf-8", page.encode())

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            try:
                r = json.loads(self.rfile.read(n))
            except Exception:
                self.send_error(400); return
            if r.get("walkway") not in VALID:
                self.send_error(400); return
            with open(HERE / labels_path, "a") as fh:
                fh.write(json.dumps({"frame": r["frame"], "walkway": r["walkway"],
                                     "by": os.environ.get("USER", "?")}) + "\n")
            self._send(200, "application/json", b'{"ok":true}')
    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", default="samples", help="directory under lab/ holding the frames")
    ap.add_argument("--labels", default="labels.jsonl")
    ap.add_argument("--port", type=int, default=8095)
    ap.add_argument("--score", action="store_true", help="score predictions, do not serve")
    a = ap.parse_args()

    if a.score:
        score(a.labels, a.set); return

    fs = frames(a.set)
    if not fs: sys.exit(f"no jpgs in lab/{a.set}/")
    done = len(load_labels(a.labels))
    print(f"{len(fs)} frames in lab/{a.set}/, {done} already labelled")
    print(f"open http://<box>:{a.port}   keys 1-5, arrows to navigate")
    print(f"when finished:  ./label.py --score")
    ThreadingHTTPServer(("0.0.0.0", a.port), make_handler(fs, a.labels)).serve_forever()


if __name__ == "__main__":
    main()
