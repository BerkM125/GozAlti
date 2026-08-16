# evidence — the artifacts behind the claims, kept because they cannot be regenerated

36 KB of text. Everything else this module produces (annotated mp4s, frame sequences,
overlays, prediction jsonl) is gitignored and rebuildable by re-running a script. These
files are not.

## `*.digest.txt` — the four video clips

Output of `video.py` over 60 s of **live HLS** from four downtown SDOT cameras, sampled
at 4 fps (240 frames each, 960 total), Sat 15 Aug ~21:00 PDT. Each digest carries the
tracker's measurements and the VLM's per-chunk captions in time order.

**Re-running does not reproduce these.** The clips were live footage of a specific
minute; pull the same cameras now and different people are on different corners. The
mp4s are 345 MB and gitignored, so these digests are the only surviving record of what
the pipeline saw.

They are the evidence for `RESULTS.md` §1, including the headline finding:

| camera | unique people in 60 s | peak in any single frame |
|---|---|---|
| CMR-0428 5th S & Jackson | **154** | 25 |
| CMR-0303 2nd & Pike | **95** | 19 |
| CMR-0176 5th & Pine | **64** | 11 |
| CMR-0261 Alaskan Way & Wall | **12** | 4 |

Stills undercount foot traffic by up to 6×. Also here: the bus tracked across four
consecutive chunks at 5th & Pine (arriving, stopping, departing, `transit_stop` each
time), which is the summarization half of VSS doing something no detector can.

## `bench_detectors.json`

Full per-frame timings from `bench_detectors.py` — three detectors × two resolution caps
× 23 frames, with the resolution bucket for each. Backs the two corrections in
`CAPABILITIES.md`: maskrcnn costs +7.8 ms rather than the +6 ms I first quoted from the
faster of two frames, and input resolution is currently irrelevant because torchvision
resizes to `min_size=800` internally.

Cheap to regenerate (about 4 minutes on the box) but kept so the numbers in the docs can
be checked without a GPU.

## Also committed, same reasoning, elsewhere in the repo

- `lab/labels.jsonl`, `lab/labels_samples.jsonl` — 78 hand-labelled frames. Costs a
  human labelling pass to recreate. See `RESULTS.md` §4b for why three of those labels
  were later found wrong and corrected.
- `lab/samples/`, `lab/eval/` — the frames themselves, since SDOT's retention prunes
  originals within hours.
