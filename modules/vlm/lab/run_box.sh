#!/usr/bin/env bash
# Run a lab script inside the vLLM container on the box, with the flags that matter.
#
#   ./run_box.sh video.py clips/*.mp4 --keypoints
#   ./run_box.sh video.py --bench clips/CMR-0176__5_Pine_EW.mp4
#   ./run_box.sh detect.py samples/*.jpg
#
# Why each flag is here:
#   --gpus all          the detector needs CUDA
#   --network host      ollama listens on the host's 127.0.0.1:11434; without this the
#                       container's loopback is its own and every VLM call fails
#   --user $(id -u):..  the container is root by default, and anything it writes into the
#                       repo becomes root-owned. That already broke a `git pull` once:
#                       the container created lab/viewer/ before git could, and git then
#                       could not write index.html into it. Running as the host user
#                       keeps every artifact owned by acer01.
#   -v /etc/passwd:ro   with --user 1000 the container has no passwd entry for that uid,
#                       and torch calls getpwuid() while resolving a cache path, which
#                       raises KeyError: 'getpwuid(): uid not found: 1000'. Mounting the
#                       host's passwd read-only is the whole fix.
#   TORCH_HOME=/tmp/..  the weights cache cannot live under /root once we drop to uid
#                       1000: torch creates its parent dir and /root is not writable,
#                       so it is mounted somewhere the user can write instead. The
#                       weights are pre-downloaded there and the box has no outbound
#                       access to torch hub during a demo.
set -euo pipefail
LAB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${IMAGE:-vllm/vllm-openai:latest}"
exec docker run --rm --gpus all --network host \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp -e TORCH_HOME=/tmp/torch -e XDG_CACHE_HOME=/tmp/cache \
  -v /etc/passwd:/etc/passwd:ro -v /etc/group:/etc/group:ro \
  -v "$LAB":/lab -w /lab \
  -v /home/acer01/junk/torchcache:/tmp/torch \
  --entrypoint python3 "$IMAGE" "$@"
