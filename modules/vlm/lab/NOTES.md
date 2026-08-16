# VLM step — findings so far (Acer GN100, ollama 0.32.13)

## Speed (qwen2.5vl:7b, ollama, single request)
- production caption prompt (JSON, ~85 tok): **~3 s/frame** warm; 13.8 s first call (model load).
  Mac M4 was 12 s/frame. 646 cameras serial ≈ 32 min; with 4 concurrent requests likely <10 min.
- people/boxes prompt: 3–17 s depending on crowd size (token count scales with people). Use `-n 1500`
  for busy frames — CMR-0176 truncated at 768 tokens → broken JSON.

## Frames
- SDOT frames are **720x480** natively (not 1080p). Mac's 1024 downscale was a no-op.
- ollama upscales small inputs internally; Qwen2.5-VL grounding coords come back in *that* space,
  so boxes drawn on the 720x480 file land in the wrong place.
- **Fix: resize long edge to 1456 (multiple of 28) before sending** (`--edge 1456`). Then coords ==
  pixels of the sent image and overlays line up (verified on CMR-0039: 8 people boxed correctly).
  1008 was not enough (y coords still overshoot).

## Output shape that worked
- `format: "json"` on the ollama request = constrained decoding; every reply parsed so far.
- caption schema (prompts/caption.txt) fills all fields; `people_visible` clusters at 10 on crowd
  frames — the model rounds. Boxes prompt gives an honest count (8 vs "10").
- boxes: `{"people":[{"bbox_2d":[x1,y1,x2,y2],"kind":...}]}` — dot = box centre works as "point".

## Bench (Sat 18:00) — see MODELS.md for the table
- qwen3-vl needs `think:false` on the request; ollama still returns the JSON in `thinking` for this model — `ask.py` falls back to that field. Works.
- gemma4 grounds fine once read as `[y,x,y,x]/1000` (first pass mis-drew it as Qwen pixels).
- points prompt (`prompts/points.txt`) is the production shape: fewer tokens, direct `cx,cy`.

## Next to try
- night / wet samples: does grounding hold in dark_lit frames?
- combine: one call for caption + points vs two calls — latency vs quality.
- dedupe/cap for points; `crowd` flag when >25.
- Nemotron-3-Nano-Omni via llama.cpp (NVIDIA-branded VLM on the box) — one run for the judges' question.
- Molmo2 pointing (points instead of boxes) needs a torch/vLLM path; not in ollama.
