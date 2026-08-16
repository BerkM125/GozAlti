#!/usr/bin/env bash
# Pull fixed-length clips from SDOT HLS cameras, one camera at a time.
#
#   ./pull_clips.sh                        # default set, 60 s each
#   ./pull_clips.sh 30 CMR-0176            # 30 s from one camera
#   SECS=60 ./pull_clips.sh CMR-0176 CMR-0303
#
# Team rate-limit rule: one camera at a time, never a retry loop against a live
# stream. This script is strictly sequential and gives up after 3 attempts.
#
# Two failure modes we actually hit and what they were:
#
#   1. `ffmpeg -i <hls> -t 60 -c copy out.mp4` produced nothing. The cause was NOT
#      the mp4 muxer and NOT the `timed_id3` metadata stream the playlist carries
#      (ffmpeg's default stream selection drops that on its own). It was DNS:
#      glibc through the systemd-resolved stub at 127.0.0.53 intermittently returns
#      "Temporary failure in name resolution" while `curl` in the same second
#      succeeds. ffmpeg then fails at input open and never creates the file — so the
#      0-byte artifact came from the shell redirect, not from ffmpeg. Measured: one
#      failure in a cold burst, then 30/30 successes. Fix = warm the resolver cache
#      with getent and retry the whole ffmpeg invocation.
#   2. The live window is only ~3 x 10 s segments, so ffmpeg pulls the first ~30 s
#      at ~135x and then tracks the live edge in real time. A 60 s clip takes ~35 s
#      of wall clock, not 60.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="61e0c5d388c2e.streamlock.net"
OUT="${OUT:-$HERE/clips}"
SECS="${SECS:-60}"

# camera_id -> stream name, from experiments/surukamera/data/cameras.json (has_stream: true)
declare -A STREAM=(
  [CMR-0176]=5_Pine_EW       # 5th Ave & Pine St EW      - dense retail pedestrians
  [CMR-0303]=2_Pike_EW       # 2nd Ave & Pike St EW      - mixed transit + pedestrians
  [CMR-0428]=5_S_Jackson     # 5th Ave S & S Jackson St  - p98 pedestrians / p9 vehicles
  [CMR-0261]=Alaskan_Wall    # Alaskan Way & Wall St     - p93 traffic, waterfront arterial
)
DEFAULT_CAMS=(CMR-0176 CMR-0303 CMR-0428 CMR-0261)

args=("$@")
if [[ ${#args[@]} -gt 0 && "${args[0]}" =~ ^[0-9]+$ ]]; then SECS="${args[0]}"; args=("${args[@]:1}"); fi
CAMS=("${args[@]}"); [[ ${#CAMS[@]} -eq 0 ]] && CAMS=("${DEFAULT_CAMS[@]}")

mkdir -p "$OUT"
ok=0; bad=0
for cam in "${CAMS[@]}"; do
  name="${STREAM[$cam]:-}"
  if [[ -z "$name" ]]; then echo "!! $cam: not in the stream table, skipping"; bad=$((bad+1)); continue; fi
  url="https://$HOST:443/live/$name.stream/playlist.m3u8"
  dst="$OUT/${cam}__${name}.mp4"
  echo "== $cam ($name) -> $dst"
  got=0
  for attempt in 1 2 3; do
    getent hosts "$HOST" >/dev/null 2>&1 || sleep 2   # warm the resolver before ffmpeg asks
    t0=$(date +%s)
    ffmpeg -hide_banner -loglevel warning -y \
      -rw_timeout 15000000 -i "$url" \
      -t "$SECS" -map 0:v:0 -c:v copy -an -dn -sn \
      -movflags +faststart -f mp4 "$dst" </dev/null
    rc=$?
    if [[ $rc -eq 0 && -s "$dst" ]]; then
      dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$dst" 2>/dev/null)
      printf "   ok  %s bytes, %.1f s content, %s s wall\n" \
        "$(stat -c%s "$dst")" "${dur:-0}" "$(( $(date +%s) - t0 ))"
      got=1; break
    fi
    echo "   attempt $attempt failed (rc=$rc); DNS through 127.0.0.53 is the usual cause"
    rm -f "$dst"; sleep 3
  done
  if [[ $got -eq 1 ]]; then ok=$((ok+1)); else echo "!! $cam: gave up after 3 attempts"; bad=$((bad+1)); fi
done
echo "== $ok clips pulled, $bad failed, ${SECS}s target, into $OUT"
