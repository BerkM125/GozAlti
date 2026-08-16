#!/usr/bin/env bash
# Run every sample through each candidate VLM with both prompts; results land in bench/<model>/.
# Usage: ./bench.sh [model ...]     (default: qwen2.5vl:7b qwen3-vl:8b gemma4:12b)
# Each model: caption.txt (production schema, --json) and people.txt (boxes, --json --draw --edge 1456).
set -u
cd "$(dirname "$0")"
MODELS=("$@"); [ ${#MODELS[@]} -eq 0 ] && MODELS=(qwen2.5vl:7b qwen3-vl:8b gemma4:12b)
mkdir -p bench
for m in "${MODELS[@]}"; do
  tag="${m//[:\/]/-}"; d="bench/$tag"; mkdir -p "$d"
  echo "##### $m  $(date +%H:%M:%S)"
  ./ask.py samples/*.jpg -m "$m" -f prompts/caption.txt --json -n 256 > "$d/caption.txt" 2>&1
  echo "  caption done $(date +%H:%M:%S)"
  ./ask.py samples/*.jpg -m "$m" -f prompts/people.txt --json --draw --edge 1456 -n 1500 > "$d/people.txt" 2>&1
  echo "  people done  $(date +%H:%M:%S)"
  # move this model's overlays under its bench dir
  mkdir -p "$d/overlays"; mv out/*"__${tag}".jpg "$d/overlays/" 2>/dev/null || true
done
echo "##### BENCH DONE $(date +%H:%M:%S)"
