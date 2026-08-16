#!/usr/bin/env python3
"""Video Search and Summarization over a traffic-camera clip.

The pipeline, in the VSS shape:

  ingest      mp4 or HLS URL -> frames sampled at --fps            (ffmpeg)
  index       detector + tracker over every sampled frame          (GPU, ~60-72 ms/frame)
              -> people boxes, silhouettes or 17-point skeletons, vehicle counts,
                 stable track ids, dwell, direction of travel, facing
  caption     clip split into --chunk second chunks; each chunk becomes one strip of
              frames and gets a dense VLM caption, with the chunk's measured numbers
              injected so the model never has to count                (VLM, ~1 call/chunk)
  summarize   one more VLM pass over the chunk captions -> whole-clip summary   (1 call)
  ask         natural-language questions answered from the captions + timeline,
              evidence-gated: "not visible" is a correct answer               (1 call each)
  render      annotated frames -> annotated mp4, timeline.json, viewer payload

The detector is not the answer here, it is the index the VLM reasons over. It supplies
the counts so the VLM never guesses a number, and the tracks so "unique people over the
minute" is a real figure rather than a per-frame maximum.

Runs entirely inside the vLLM container, which has torch, torchvision, CUDA, PIL, cv2
AND ffmpeg. `--network host` is required so the container can reach ollama on the host:

  docker run --rm --gpus all --network host \
    -v /home/acer01/GozAlti/modules/vlm/lab:/lab -w /lab \
    -v /home/acer01/junk/torchcache:/root/.cache/torch \
    --entrypoint python3 vllm/vllm-openai:latest video.py clips/CMR-0176__5_Pine_EW.mp4

Nothing here scores danger, and the VLM is instructed never to judge the people in frame.
"""
import argparse, base64, json, math, os, re, shutil, subprocess, sys, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import detlib, track as tracklib

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OPENAI = os.environ.get("OPENAI_URL", "http://127.0.0.1:8000/v1")

DEFAULT_QUESTIONS = [
    "Was the sidewalk ever blocked or narrowed during this minute?",
    "When was the scene busiest, and what was happening at that moment?",
    "Did any vehicle stop, park, or unload across the pedestrian path?",
    "Was there any construction, closure, or emergency response visible?",
]


def sh(cmd, **kw):
    """Run a command, raise with its stderr attached if it fails."""
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed ({p.returncode}):\n{p.stderr[-2000:]}")
    return p.stdout


def probe(path):
    out = sh(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
              "stream=width,height,r_frame_rate", "-show_entries", "format=duration",
              "-of", "json", str(path)])
    j = json.loads(out)
    st = j["streams"][0]
    num, den = st["r_frame_rate"].split("/")
    return {"width": st["width"], "height": st["height"],
            "src_fps": round(float(num) / float(den), 3),
            "duration_s": round(float(j["format"]["duration"]), 3)}


# ------------------------------------------------------------------ ingest

def ingest(src, work, fps, seconds=None):
    """mp4 (or HLS URL) -> raw/f%05d.jpg at `fps`. Returns (frame paths, meta, ms)."""
    raw = work / "raw"
    if raw.exists():
        shutil.rmtree(raw)
    raw.mkdir(parents=True)
    t0 = time.perf_counter()
    if str(src).startswith("http"):
        clip = work / "source.mp4"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
               "-rw_timeout", "15000000", "-i", str(src), "-t", str(seconds or 60),
               "-map", "0:v:0", "-c:v", "copy", "-an", "-dn", "-sn",
               "-movflags", "+faststart", "-f", "mp4", str(clip)]
        sh(cmd)
        src = clip
    meta = probe(src)
    sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
        "-vf", f"fps={fps}", "-q:v", "2", str(raw / "f%05d.jpg")])
    frames = sorted(raw.glob("f*.jpg"))
    meta["source"] = str(src)
    return frames, meta, (time.perf_counter() - t0) * 1000


# ------------------------------------------------------------------ index

def index_frames(frames, fps, arch, min_size, person_thresh, vehicle_thresh,
                 device, want_keypoints, mask_polys):
    """Detector over every frame, then the tracker over the result.

    Returns (per_frame rows, track summaries, timing dict). Two detector passes only
    when the primary arch cannot produce keypoints and they were asked for; the
    keypoint boxes are grafted onto the primary boxes by best IoU.
    """
    import torch
    dev = device if torch.cuda.is_available() else "cpu"
    prim = detlib.load(arch, min(person_thresh, vehicle_thresh), dev, min_size)
    kp_det = None
    if want_keypoints and not prim.has_keypoints:
        kp_det = detlib.load("keypointrcnn", person_thresh, dev, min_size)

    # warm every graph so the first real frame is not the slow one
    for d in (prim, kp_det):
        if d:
            t, W, H = detlib.read_tensor(d, frames[0])
            detlib.infer(d, t)

    per_frame, prim_ms, kp_ms = [], [], []
    tr = tracklib.Tracker.for_fps(fps)
    W = H = None
    for i, f in enumerate(frames):
        t_s = i / fps
        tensor, W, H = detlib.read_tensor(prim, f)
        out, ms = detlib.infer(prim, tensor)
        prim_ms.append(ms)
        people, vehicles = detlib.parse(prim, out, W, H, person_thresh, vehicle_thresh,
                                        mask_polys=mask_polys)
        if kp_det is not None and people:
            kt, kW, kH = detlib.read_tensor(kp_det, f)
            kout, kms = detlib.infer(kp_det, kt)
            kp_ms.append(kms)
            kpeople, _ = detlib.parse(kp_det, kout, kW, kH, person_thresh, 1.1)
            _graft(people, kpeople)
        elif kp_det is not None:
            kp_ms.append(0.0)
        assign = tr.update(i, t_s, people, W, H)
        for p, tid in zip(people, assign):
            p["track"] = tid
        sc = detlib.score(people, vehicles)
        per_frame.append({"i": i, "t": round(t_s, 3), "file": f.name,
                          "ms": round(ms, 1), "detections": people, **sc})

    tracks = tr.summary(fps)
    timing = {"detector_ms_mean": round(sum(prim_ms) / len(prim_ms), 1),
              "detector_ms_median": round(sorted(prim_ms)[len(prim_ms) // 2], 1),
              "detector_ms_p90": round(sorted(prim_ms)[int(len(prim_ms) * 0.9)], 1),
              "detector_arch": arch, "detector_min_size": prim.min_size,
              "tracker": {"gate_bh": round(tr.gate_bh, 3), "max_age": tr.max_age,
                          "min_hits": tr.min_hits, "iou_thresh": tr.iou_thresh,
                          "stitch_joins": getattr(tr, "joins", 0)}}
    if kp_ms:
        nz = [m for m in kp_ms if m > 0] or [0]
        timing.update({"keypoint_ms_mean": round(sum(nz) / len(nz), 1),
                       "keypoint_frames": len(nz),
                       "per_frame_total_ms": round(timing["detector_ms_mean"]
                                                   + sum(kp_ms) / len(kp_ms), 1)})
    else:
        timing["per_frame_total_ms"] = timing["detector_ms_mean"]
    return per_frame, tracks, timing, (W, H)


def _graft(people, kpeople, iou_thresh=0.45):
    """Copy keypoints + facing from the keypoint model's boxes onto the primary boxes."""
    used = set()
    for p in people:
        best, bi = 0.0, None
        for j, k in enumerate(kpeople):
            if j in used:
                continue
            o = tracklib.iou(p["box"], k["box"])
            if o > best:
                best, bi = o, j
        if bi is not None and best >= iou_thresh:
            used.add(bi)
            for key in ("keypoints", "n_keypoints", "facing", "facing_deg",
                        "shoulder_ratio", "shoulder_px"):
                if key in kpeople[bi]:
                    p[key] = kpeople[bi][key]


# ------------------------------------------------------------------ chunking

def chunk_frames(per_frame, chunk_s, duration):
    """Split sampled frames into fixed-length chunks with their own measured numbers."""
    chunks = []
    n = max(1, math.ceil(duration / chunk_s))
    for c in range(n):
        t0, t1 = c * chunk_s, min(duration, (c + 1) * chunk_s)
        rows = [f for f in per_frame if t0 <= f["t"] < t1 or (c == n - 1 and f["t"] >= t0)]
        if not rows:
            continue
        counts = [r["people_count"] for r in rows]
        peak = rows[counts.index(max(counts))]
        veh = {}
        for r in rows:
            for k, v in r["vehicles"].items():
                veh[k] = max(veh.get(k, 0), v)
        chunks.append({"chunk": c, "t0": round(t0, 2), "t1": round(t1, 2),
                       "frames": [r["i"] for r in rows],
                       "people_peak": max(counts), "people_peak_at": peak["t"],
                       "people_mean": round(sum(counts) / len(counts), 2),
                       "vehicles_peak": max(r["vehicle_count"] for r in rows),
                       "vehicles_by_type_peak": {k: v for k, v in veh.items() if v},
                       "peak_frame": peak["i"]})
    return chunks


def strip_image(frames, per_frame, chunk, out_path, tile_w=560, cols=2):
    """Tile a chunk's frames into one time-ordered strip for the VLM.

    A single frame cannot show motion. A strip can, and one VLM call over a strip costs
    what one call over one frame costs — which is the whole reason the captions are per
    chunk rather than per frame.
    """
    from PIL import Image, ImageDraw
    idxs = chunk["frames"]
    pick = _spread(idxs, 4)
    if chunk["peak_frame"] not in pick:
        pick[len(pick) // 2] = chunk["peak_frame"]
        pick = sorted(set(pick))
    ims = []
    for i in pick:
        row = next(r for r in per_frame if r["i"] == i)
        im = Image.open(frames[i]).convert("RGB")
        s = tile_w / im.width
        im = im.resize((tile_w, max(1, round(im.height * s))), Image.LANCZOS)
        d = ImageDraw.Draw(im)
        lab = f"{int(row['t'] // 60)}:{int(row['t'] % 60):02d}"
        d.rectangle([0, 0, 46, 15], fill=(0, 0, 0))
        d.text((4, 3), lab, fill=(255, 255, 255))
        ims.append(im)
    cols = min(cols, len(ims))
    rows_n = math.ceil(len(ims) / cols)
    tw, th = ims[0].width, ims[0].height
    sheet = Image.new("RGB", (cols * tw, rows_n * th), (12, 12, 14))
    for k, im in enumerate(ims):
        sheet.paste(im, ((k % cols) * tw, (k // cols) * th))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, quality=86)
    return out_path, pick


def _spread(seq, k):
    if len(seq) <= k:
        return list(seq)
    step = (len(seq) - 1) / (k - 1)
    return [seq[round(i * step)] for i in range(k)]


# ------------------------------------------------------------------ VLM

def vlm(model, prompt, image, max_tokens, api, timeout=600, num_ctx=16384):
    """One VLM call. `image` may be None for the text-only summarize/ask passes.

    `num_ctx` matters more than it looks. ollama defaults qwen3-vl:8b to its full
    262144-token context, which reserves 44.5 GB of KV cache on this box — measured,
    and enough to push the detector into CUDA OOM on the shared 121 GB. 16k is far
    more than a strip image plus our digest needs and costs about 2 GB.
    """
    if api == "openai":
        content = [{"type": "text", "text": prompt}]
        if image:
            b64 = base64.b64encode(Path(image).read_bytes()).decode()
            content.insert(0, {"type": "image_url",
                               "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        body = {"model": model, "temperature": 0, "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": content}]}
        url = f"{OPENAI}/chat/completions"
    else:
        body = {"model": model, "prompt": prompt, "stream": False, "format": "json",
                "keep_alive": "24h", "think": False,
                "options": {"temperature": 0, "num_predict": max_tokens,
                            "num_ctx": num_ctx}}
        if image:
            body["images"] = [base64.b64encode(Path(image).read_bytes()).decode()]
        url = f"{OLLAMA}/api/generate"
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer none"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.load(r)
    text = (out["choices"][0]["message"].get("content") if api == "openai"
            else (out.get("response") or out.get("thinking") or ""))
    return text, round(time.perf_counter() - t0, 2)


def parse_json(text):
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text or "", re.S)
        if not m:
            return None
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", m.group(0)))
        except Exception:
            return None


def mmss(t):
    return f"{int(t // 60)}:{int(t % 60):02d}"


def chunk_context(chunk, tracks):
    """The measured numbers for one chunk, phrased as given facts the VLM must not recount."""
    inside = [t for t in tracks if t["confirmed"]
              and t["t_end"] >= chunk["t0"] and t["t_start"] < chunk["t1"]]
    entered = [t for t in inside if chunk["t0"] <= t["t_start"] < chunk["t1"]]
    v = chunk["vehicles_by_type_peak"]
    L = [f"These frames span {mmss(chunk['t0'])} to {mmss(chunk['t1'])} of the clip.",
         f"A detector counted at most {chunk['people_peak']} people outside vehicles here "
         f"(mean {chunk['people_mean']} per sampled frame) and at most "
         f"{chunk['vehicles_peak']} vehicles"
         + (f" ({', '.join(f'{n} {k}' for k, n in v.items())})" if v else "") + "."]
    if inside:
        moving = [t for t in inside if t["motion"] == "walking"]
        still = [t for t in inside if t["motion"] == "standing"]
        L.append(f"A tracker followed {len(inside)} distinct people through this stretch, "
                 f"{len(entered)} of whom first appear in it; {len(moving)} were moving at "
                 f"walking pace and {len(still)} were about stationary.")
        dirs = {}
        for t in moving:
            dirs[t["travel_label"]] = dirs.get(t["travel_label"], 0) + 1
        if dirs:
            L.append("Directions of travel across the frame: "
                     + ", ".join(f"{n} {d}" for d, n in sorted(dirs.items(), key=lambda x: -x[1]))
                     + ".")
    else:
        L.append("The tracker followed no people through this stretch.")
    return "\n".join(L) + "\n\n"


def caption_chunks(chunks, frames, per_frame, tracks, work, model, api, max_tokens, num_ctx):
    base = (HERE / "prompts" / "chunk_caption.txt").read_text()
    secs, fails = [], 0
    for ch in chunks:
        strip, picked = strip_image(frames, per_frame, ch, work / "strips" / f"chunk{ch['chunk']:02d}.jpg")
        text, s = vlm(model, chunk_context(ch, tracks) + base, strip, max_tokens, api,
                      num_ctx=num_ctx)
        cap = parse_json(text)
        secs.append(s)
        ch["strip"] = strip.name
        ch["strip_frames"] = picked
        ch["vlm_seconds"] = s
        if cap is None:
            fails += 1
            ch["caption"] = None
            ch["parse_error"] = (text or "")[:200]
            print(f"  chunk {ch['chunk']} {mmss(ch['t0'])}  PARSE FAIL after {s}s")
            continue
        ch["caption"] = cap
        print(f"  chunk {ch['chunk']} {mmss(ch['t0'])}-{mmss(ch['t1'])} [{s}s] "
              f"{ch['people_peak']}p/{ch['vehicles_peak']}v · {cap.get('caption', '')[:110]}")
    return secs, fails


def captions_digest(chunks, agg, meta, camera):
    """The text the summarize and ask passes reason over. Captions plus counted facts."""
    L = [f"CAMERA {camera or 'unknown'} — {meta.get('location', '')}".rstrip(" —"),
         f"CLIP: {agg['duration_s']:.0f} s, sampled at {agg['sample_fps']} fps "
         f"({agg['frames_sampled']} frames analysed).",
         "",
         "MEASURED OVER THE WHOLE CLIP (detector and tracker, not estimates):",
         f"- {agg['unique_people']} unique people tracked through the clip.",
         f"- People visible per frame: peak {agg['people_peak']} at {mmss(agg['people_peak_at_s'])}, "
         f"mean {agg['people_mean']}, minimum {agg['people_min']}.",
         f"- Vehicles per frame: peak {agg['vehicles_peak']}, mean {agg['vehicles_mean']}.",
         f"- Time on screen per person: median {agg['dwell_median_s']} s, longest {agg['dwell_max_s']} s.",
         f"- Movement: " + ", ".join(f"{n} {k}" for k, n in agg["motion_mix"].items()) + ".",
         ]
    if agg["travel_mix"]:
        L.append("- Directions of travel across the frame: "
                 + ", ".join(f"{n} {k}" for k, n in sorted(agg["travel_mix"].items(),
                                                           key=lambda x: -x[1])) + ".")
    L += ["", "CHUNK CAPTIONS, IN TIME ORDER:"]
    for ch in chunks:
        c = ch.get("caption")
        if not c:
            L.append(f"[{mmss(ch['t0'])}-{mmss(ch['t1'])}] (no caption: the model's reply "
                     f"did not parse)")
            continue
        L.append(f"[{mmss(ch['t0'])}-{mmss(ch['t1'])}] peak {ch['people_peak']} people, "
                 f"{ch['vehicles_peak']} vehicles. {c.get('caption', '')}")
        if c.get("change"):
            L.append(f"    change across the chunk: {c['change']}")
        L.append(f"    scene {c.get('scene', '?')}; walking path {c.get('walkway_status', '?')}"
                 + (f" ({c['walkway_reason']})" if c.get("walkway_reason") else ""))
        ev = [e for e in (c.get("events") or []) if e != "none"]
        if ev:
            L.append(f"    events: {', '.join(ev)}")
        if c.get("setting_notes"):
            L.append(f"    setting: {c['setting_notes']}")
    return "\n".join(L)


def summarize(digest, model, api, max_tokens, num_ctx):
    base = (HERE / "prompts" / "clip_summary.txt").read_text()
    text, s = vlm(model, base + "\n\nMATERIAL:\n" + digest, None, max_tokens, api,
                  num_ctx=num_ctx)
    return parse_json(text), s, text


def ask(digest, question, model, api, max_tokens, num_ctx):
    base = (HERE / "prompts" / "clip_qa.txt").read_text()
    prompt = base + "\n\nMATERIAL:\n" + digest + f"\n\nQUESTION: {question}\n"
    text, s = vlm(model, prompt, None, max_tokens, api, num_ctx=num_ctx)
    return parse_json(text), s, text


# ------------------------------------------------------------------ render

def render(frames, per_frame, work, fps, trail_len=8):
    """Annotated jpgs with track ids, trails and skeletons; then an mp4 of them."""
    outdir = work / "frames"
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True)
    hist = {}
    t0 = time.perf_counter()
    for row, f in zip(per_frame, frames):
        for p in row["detections"]:
            hist.setdefault(p["track"], []).append((p["cx"], p["cy"]))
        live = {p["track"] for p in row["detections"]}
        trails = {k: v[-trail_len:] for k, v in hist.items() if k in live and len(v) > 1}
        note = (f"t={mmss(row['t'])}  {row['people_count']} people  "
                f"{row['vehicle_count']} vehicles  det {row['ms']:.0f}ms")
        detlib.draw(f, row["detections"], note, outdir / f"a{row['i']:05d}.jpg",
                    tracks_by_index=[p["track"] for p in row["detections"]], trails=trails)
    draw_ms = (time.perf_counter() - t0) * 1000 / max(1, len(frames))
    mp4 = work / "annotated.mp4"
    t1 = time.perf_counter()
    sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-framerate", str(fps), "-pattern_type", "glob", "-i", str(outdir / "a*.jpg"),
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "23", "-pix_fmt", "yuv420p", "-r", "25", "-movflags", "+faststart", str(mp4)])
    return mp4, round(draw_ms, 1), round((time.perf_counter() - t1) * 1000, 1)


def publish(work, clip_id, viewer_data):
    """Copy what the viewer needs into lab/viewer/data/<clip_id>/ so it is self-contained."""
    dst = viewer_data / clip_id
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("timeline.json", "annotated.mp4"):
        src = work / name
        if src.exists():
            shutil.copy2(src, dst / name)
    sdir = work / "strips"
    if sdir.exists():
        (dst / "strips").mkdir(exist_ok=True)
        for s in sdir.glob("*.jpg"):
            shutil.copy2(s, dst / "strips" / s.name)
    return dst


# ------------------------------------------------------------------ main

def run_clip(src, a, viewer_data):
    clip_id = Path(src).stem if not str(src).startswith("http") else re.sub(r"\W+", "_", str(src))[-40:]
    camera = clip_id.split("__")[0] if "__" in clip_id else None
    work = HERE / a.out / clip_id
    work.mkdir(parents=True, exist_ok=True)
    print(f"\n=== {clip_id}")

    frames, meta, ing_ms = ingest(src, work, a.fps, a.seconds)
    if not frames:
        print("  no frames extracted; skipping")
        return None
    print(f"  ingest: {len(frames)} frames @ {a.fps} fps from {meta['duration_s']}s "
          f"{meta['width']}x{meta['height']} @ {meta['src_fps']}fps src  [{ing_ms:.0f}ms]")

    per_frame, tracks, timing, (W, H) = index_frames(
        frames, a.fps, a.arch, a.min_size, a.person_thresh, a.vehicle_thresh,
        a.device, a.keypoints, a.mask_polys)
    agg = tracklib.aggregate(per_frame, tracks, a.fps, meta["duration_s"])
    print(f"  index:  {timing['detector_ms_mean']}ms/frame {a.arch}"
          + (f" + {timing.get('keypoint_ms_mean')}ms keypointrcnn" if a.keypoints and not
             a.arch == "keypointrcnn" else "")
          + f"  ->  {agg['unique_people']} unique people, peak {agg['people_peak']} in frame")

    chunks = chunk_frames(per_frame, a.chunk, meta["duration_s"])
    cap_secs, cap_fails = [], 0
    summary = qa = None
    sum_s = 0.0
    qa_rows = []
    if not a.no_vlm:
        print(f"  caption: {len(chunks)} chunks of {a.chunk}s -> {a.model}")
        cap_secs, cap_fails = caption_chunks(chunks, frames, per_frame, tracks, work,
                                             a.model, a.api, a.max_tokens, a.num_ctx)
        digest = captions_digest(chunks, agg, meta, camera)
        (work / "digest.txt").write_text(digest)
        summary, sum_s, raw = summarize(digest, a.model, a.api, a.max_tokens + 300, a.num_ctx)
        if summary:
            print(f"  summary [{sum_s}s]: {summary.get('headline', '')}")
        else:
            print(f"  summary [{sum_s}s]: PARSE FAIL: {raw[:160]}")
        for q in (a.ask or DEFAULT_QUESTIONS):
            ans, qs, raw = ask(digest, q, a.model, a.api, a.max_tokens, a.num_ctx)
            qa_rows.append({"question": q, "answer": ans, "seconds": qs,
                            "parse_error": None if ans else (raw or "")[:200]})
            mark = "" if (ans or {}).get("supported") else "  [not supported by footage]"
            print(f"  ask [{qs}s] {q}\n      {(ans or {}).get('answer', 'PARSE FAIL')[:150]}{mark}")

    mp4, draw_ms, enc_ms = render(frames, per_frame, work, a.fps)

    src_fps = meta["src_fps"]
    per_frame_ms = timing["per_frame_total_ms"]
    doc = {
        "clip_id": clip_id, "camera_id": camera, "source": str(src),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "video": meta, "config": {
            "sample_fps": a.fps, "chunk_s": a.chunk, "arch": a.arch,
            "min_size": timing["detector_min_size"], "keypoints": bool(a.keypoints),
            "person_thresh": a.person_thresh, "vehicle_thresh": a.vehicle_thresh,
            "vlm_model": a.model if not a.no_vlm else None, "vlm_api": a.api},
        "aggregate": agg,
        "throughput": {
            **timing,
            "ingest_ms_total": round(ing_ms, 1),
            "annotate_ms_per_frame": draw_ms,
            "encode_ms_total": enc_ms,
            "detector_fps": round(1000.0 / per_frame_ms, 2),
            "source_fps": src_fps,
            "realtime_at_source_fps": (1000.0 / per_frame_ms) >= src_fps,
            "realtime_at_sample_fps": (1000.0 / per_frame_ms) >= a.fps,
            "realtime_note": (f"{1000.0 / per_frame_ms:.1f} detector fps vs {src_fps} source fps "
                              f"and {a.fps} sampled fps; GPU stage only, ingest and encode excluded"),
            "vlm_chunk_seconds": [round(s, 2) for s in cap_secs],
            "vlm_chunk_mean_s": round(sum(cap_secs) / len(cap_secs), 2) if cap_secs else None,
            "vlm_summary_s": sum_s or None,
            "vlm_calls": len(cap_secs) + (1 if summary is not None else 0) + len(qa_rows),
            "vlm_total_s": round(sum(cap_secs) + (sum_s or 0)
                                 + sum(r["seconds"] for r in qa_rows), 2),
            "vlm_parse_fails": cap_fails,
        },
        "chunks": chunks, "summary": summary, "qa": qa_rows,
        "tracks": tracks,
        "timeline": [{"i": r["i"], "t": r["t"], "people": r["people_count"],
                      "vehicles": r["vehicle_count"], "by_type": {k: v for k, v in
                                                                  r["vehicles"].items() if v},
                      "conf": r["visibility_proxy"], "ms": r["ms"],
                      "track_ids": sorted({p["track"] for p in r["detections"]})}
                     for r in per_frame],
    }
    (work / "timeline.json").write_text(json.dumps(doc, indent=1))
    if a.frames_json:
        (work / "frames.json").write_text(json.dumps(per_frame))
    publish(work, clip_id, viewer_data)
    print(f"  render: {mp4.name} ({draw_ms:.0f}ms/frame annotate, {enc_ms:.0f}ms encode)")
    return doc


def bench_detectors(src, a):
    """Run every person-capable arch over the same frames, with the ORDER counterbalanced.

    The first cut of this bench ran each config once in a fixed order and produced
    nonsense: maskrcnn "faster" than fasterrcnn, and the same keypointrcnn config
    123.7 ms on a 720x480 clip but 65.9 ms on a 1080p one. Every config was simply
    faster than the one before it, all the way down the run — the GB10 ramps clocks
    over the first tens of seconds of GPU work, so a fixed-order sweep measures
    position in the run and not the model. Fix: one long global warmup, then sweep
    the config list forwards and backwards and keep the min per config, which cancels
    a monotonic drift instead of baking it in.

    Recall is reported next to latency because latency alone picks the wrong model.
    `people_small` counts detections under 5% of frame height — the distant
    pedestrians that everything misses at the default resize.
    """
    work = HERE / a.out / (Path(src).stem + "__bench")
    work.mkdir(parents=True, exist_ok=True)
    frames, meta, _ = ingest(src, work, a.fps, a.seconds)
    frames = frames[: a.bench_frames]
    print(f"\n=== bench on {Path(src).name}: {len(frames)} frames "
          f"{meta['width']}x{meta['height']}, source {meta['src_fps']} fps")
    import torch
    dev = a.device if torch.cuda.is_available() else "cpu"

    configs = [(arch, ms) for arch in a.bench_archs for ms in a.bench_min_sizes]
    dets = {c: detlib.load(c[0], a.person_thresh, dev,
                           None if c[1] == 800 else c[1]) for c in configs}

    # global warmup: get the GPU to its steady clock before anything is timed
    warm_t, _, _ = detlib.read_tensor(dets[configs[0]], frames[0])
    t0 = time.perf_counter()
    n = 0
    while time.perf_counter() - t0 < a.bench_warmup_s:
        detlib.infer(dets[configs[0]], warm_t)
        n += 1
    print(f"  warmup: {n} passes in {a.bench_warmup_s}s before timing")

    def sweep(order):
        res = {}
        for c in order:
            det = dets[c]
            times, ppl, small, conf, veh = [], 0, 0, [], 0
            for f in frames:
                tt, W, H = detlib.read_tensor(det, f)
                out, msec = detlib.infer(det, tt)
                times.append(msec)
                pe, ve = detlib.parse(det, out, W, H, a.person_thresh, a.vehicle_thresh,
                                      want_masks=False)
                ppl += len(pe)
                small += sum(1 for x in pe if x.get("h", 1) < 0.05)
                conf += [x["conf"] for x in pe]
                veh += sum(ve.values())
            res[c] = {"ms_mean": round(sum(times) / len(times), 1),
                      "ms_median": round(sorted(times)[len(times) // 2], 1),
                      "people_total": ppl, "people_small": small,
                      "mean_conf": round(sum(conf) / len(conf), 3) if conf else None,
                      "vehicle_total": veh}
        return res

    fwd = sweep(configs)
    rev = sweep(list(reversed(configs)))
    rows = []
    for c in configs:
        a1, a2 = fwd[c], rev[c]
        best = a1 if a1["ms_mean"] <= a2["ms_mean"] else a2
        det = dets[c]
        rows.append({"arch": c[0], "min_size": det.min_size, "frames": len(frames),
                     "people_only": det.people_only, "masks": det.has_masks,
                     "keypoints": det.has_keypoints,
                     "ms_forward": a1["ms_mean"], "ms_reverse": a2["ms_mean"],
                     "fps": round(1000.0 / best["ms_mean"], 2),
                     "realtime_at_source": (1000.0 / best["ms_mean"]) >= meta["src_fps"],
                     **best})
    print(f"  {'arch':13} {'min_size':>8} {'ms':>7} {'fps':>7} {'people':>7} "
          f"{'small':>6} {'conf':>6} {'veh':>6}   fwd/rev ms")
    for r in rows:
        print(f"  {r['arch']:13} {r['min_size']:8} {r['ms_mean']:7.1f} {r['fps']:7.2f} "
              f"{r['people_total']:7} {r['people_small']:6} "
              f"{(r['mean_conf'] if r['mean_conf'] is not None else 0):6.3f} "
              f"{r['vehicle_total']:6}   {r['ms_forward']:.0f}/{r['ms_reverse']:.0f}")
    out = {"clip": Path(src).name, "video": meta, "frames": len(frames),
           "warmup_s": a.bench_warmup_s, "rows": rows}
    (work / "bench.json").write_text(json.dumps(out, indent=1))
    for d in dets.values():
        del d
    torch.cuda.empty_cache()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clips", nargs="+", help="mp4 paths or HLS URLs")
    ap.add_argument("--fps", type=float, default=2.0, help="frames sampled per second")
    ap.add_argument("--chunk", type=float, default=10.0, help="seconds per VLM caption chunk")
    ap.add_argument("--seconds", type=int, default=60, help="length to pull when given a URL")
    ap.add_argument("--arch", default="fasterrcnn", choices=list(detlib.ARCHS))
    ap.add_argument("--min-size", type=int, default=None,
                    help="override torchvision's internal 800 px resize")
    ap.add_argument("--keypoints", action="store_true",
                    help="second pass with keypointrcnn for facing direction")
    ap.add_argument("--mask-polys", action="store_true", help="store silhouette polygons (maskrcnn)")
    ap.add_argument("--person-thresh", type=float, default=0.5)
    ap.add_argument("--vehicle-thresh", type=float, default=0.5)
    ap.add_argument("-m", "--model", default=os.environ.get("VLM", "qwen3-vl:8b"))
    ap.add_argument("--api", choices=["ollama", "openai"], default=os.environ.get("VLM_API", "ollama"))
    ap.add_argument("-n", "--max-tokens", type=int, default=420)
    ap.add_argument("--num-ctx", type=int, default=16384,
                    help="ollama context window; the default 262144 reserves 44.5 GB of "
                         "KV cache on this box and starves the detector")
    ap.add_argument("--ask", action="append", help="question over the clip; repeatable")
    ap.add_argument("--no-vlm", action="store_true", help="index only, skip captions/summary/Q&A")
    ap.add_argument("--frames-json", action="store_true", help="also dump full per-frame detections")
    ap.add_argument("--out", default="video_out")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--bench", action="store_true", help="detector bench instead of the pipeline")
    ap.add_argument("--bench-frames", type=int, default=24)
    ap.add_argument("--bench-warmup-s", type=float, default=20.0,
                    help="seconds of untimed GPU work before benching; the GB10 "
                         "ramps clocks and a short warmup measures the ramp")
    ap.add_argument("--bench-archs", default="fasterrcnn,maskrcnn,keypointrcnn")
    ap.add_argument("--bench-min-sizes", default="800")
    a = ap.parse_args()
    a.bench_archs = [s.strip() for s in a.bench_archs.split(",") if s.strip()]
    a.bench_min_sizes = [int(s) for s in a.bench_min_sizes.split(",") if s.strip()]

    viewer_data = HERE / "viewer" / "data"
    viewer_data.mkdir(parents=True, exist_ok=True)

    if a.bench:
        allb = [bench_detectors(c, a) for c in a.clips]
        (HERE / a.out / "bench_video.json").write_text(json.dumps(allb, indent=1))
        print(f"\nwrote {a.out}/bench_video.json")
        return

    docs = []
    for c in a.clips:
        try:
            d = run_clip(c, a, viewer_data)
            if d:
                docs.append(d)
        except Exception as e:
            print(f"!! {c}: {type(e).__name__}: {e}")
    if not docs:
        sys.exit("no clips processed")

    idx = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "clips": [{"clip_id": d["clip_id"], "camera_id": d["camera_id"],
                      "duration_s": d["video"]["duration_s"],
                      "resolution": f"{d['video']['width']}x{d['video']['height']}",
                      "src_fps": d["video"]["src_fps"],
                      "unique_people": d["aggregate"]["unique_people"],
                      "people_peak": d["aggregate"]["people_peak"],
                      "vehicles_peak": d["aggregate"]["vehicles_peak"],
                      "detector_ms": d["throughput"]["per_frame_total_ms"],
                      "headline": (d["summary"] or {}).get("headline")} for d in docs]}
    (viewer_data / "index.json").write_text(json.dumps(idx, indent=1))

    print("\n" + "=" * 78)
    print(f"{'clip':30} {'uniq':>5} {'peak':>5} {'veh':>4} {'ms/f':>7} {'det fps':>8} {'VLM s':>7}")
    for d in docs:
        ag, th = d["aggregate"], d["throughput"]
        print(f"{d['clip_id'][:30]:30} {ag['unique_people']:5} {ag['people_peak']:5} "
              f"{ag['vehicles_peak']:4} {th['per_frame_total_ms']:7.1f} {th['detector_fps']:8.2f} "
              f"{th['vlm_total_s'] or 0:7.1f}")
    tot = sum(d["throughput"]["vlm_total_s"] or 0 for d in docs)
    print(f"\nviewer payload -> viewer/data/  ({len(docs)} clips, {tot:.0f}s of VLM time total)")


if __name__ == "__main__":
    main()
