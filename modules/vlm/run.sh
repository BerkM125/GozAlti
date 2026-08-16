#!/usr/bin/env bash
# vlm module — install and run on the GB10 box. Everything machine-side lives here.
#
#   ./run.sh install    one-time: pull detector weights, verify the container, warm ollama
#   ./run.sh start      start the service on :8040 (docker, --network host)
#   ./run.sh stop       stop it
#   ./run.sh restart    stop + start (use after a git pull)
#   ./run.sh test       unit tests, then live tests against :8040
#   ./run.sh logs       follow the service log
#   ./run.sh status     health + what is listening
#
# Code is never edited here. Edit on the Mac, push, then:  git pull && ./run.sh restart
set -uo pipefail
cd "$(dirname "$0")"
REPO_ROOT="$(cd ../.. && pwd)"
IMAGE="vllm/vllm-openai:latest"
NAME="vlm-svc"
CACHE="$HOME/junk/torchcache"
LOG="$HOME/junk/vlm-svc.log"
PORT="${VLM_PORT:-8040}"

have_docker() { command -v docker >/dev/null 2>&1; }

install() {
  echo "== vlm install =="
  have_docker || { echo "docker missing"; exit 1; }
  docker image inspect "$IMAGE" >/dev/null 2>&1 \
    && echo "  image present: $IMAGE" \
    || { echo "  pulling $IMAGE (30 GB)"; docker pull "$IMAGE"; }

  mkdir -p "$CACHE/hub/checkpoints"
  # torchvision weights: fetched host-side because the container often has no DNS,
  # and because /root inside the container is not writable by our uid.
  for u in \
    https://download.pytorch.org/models/fasterrcnn_resnet50_fpn_v2_coco-dd69338a.pth \
    https://download.pytorch.org/models/maskrcnn_resnet50_fpn_v2_coco-73cbd019.pth \
    https://download.pytorch.org/models/keypointrcnn_resnet50_fpn_coco-fc266e95.pth
  do
    f="$CACHE/hub/checkpoints/$(basename "$u")"
    if [ -s "$f" ]; then echo "  weights ok: $(basename "$u")"
    else echo "  fetching $(basename "$u")"; curl -sSL -o "$f" "$u" || echo "  FAILED (need network)"; fi
  done

  echo "  checking GPU inside the container..."
  docker run --rm --gpus all --entrypoint python3 "$IMAGE" -c \
    'import torch,torchvision,cv2;print(f"    torch {torch.__version__} tv {torchvision.__version__} cv2 {cv2.__version__} cuda={torch.cuda.is_available()}")' \
    2>/dev/null | tail -1 || echo "    container GPU check FAILED"

  echo "  warming the VLM in ollama..."
  curl -s -m 300 http://127.0.0.1:11434/api/generate \
    -d "{\"model\":\"${VLM_MODEL:-qwen3-vl:8b}\",\"prompt\":\"\",\"keep_alive\":\"24h\"}" \
    >/dev/null && echo "    ollama warm" || echo "    ollama not reachable on :11434"
  echo "install done"
}

start() {
  docker rm -f "$NAME" >/dev/null 2>&1
  # Mount the repo at its REAL host path, not /repo. media-ingest hands us absolute
  # host paths in FrameRecord.path; if the container sees a different prefix those
  # 404. Identical paths inside and out is the only version that cannot skew.
  # DATA_MOUNT covers a frame store living outside the repo.
  local extra=()
  [ -n "${DATA_MOUNT:-}" ] && extra=(-v "$DATA_MOUNT":"$DATA_MOUNT")
  docker run -d --name "$NAME" --gpus all --network host --ipc=host \
    -v "$REPO_ROOT":"$REPO_ROOT" -w "$REPO_ROOT/modules/vlm" \
    "${extra[@]}" \
    -v "$CACHE":/root/.cache/torch \
    -e GOZALTI_ROOT="$REPO_ROOT" -e VLM_PORT="$PORT" \
    -e VLM_MODEL="${VLM_MODEL:-qwen3-vl:8b}" \
    -e VLM_DET_ARCH="${VLM_DET_ARCH:-fasterrcnn}" \
    --entrypoint python3 "$IMAGE" service.py >/dev/null || { echo "start failed"; exit 1; }
  echo -n "waiting for :$PORT "
  for i in $(seq 1 60); do
    if curl -sf -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
      echo; curl -s "http://127.0.0.1:$PORT/health" | python3 -m json.tool; return 0
    fi
    echo -n "."; sleep 2
  done
  echo " TIMEOUT"; docker logs --tail 20 "$NAME"; exit 1
}

stop() { docker rm -f "$NAME" >/dev/null 2>&1 && echo "stopped" || echo "not running"; }

test_all() {
  echo "== unit tests =="
  python3 test_service.py || return 1
  echo
  echo "== live tests (:$PORT) =="
  curl -sf -m 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 \
    || { echo "service not up — ./run.sh start first"; return 1; }
  python3 test_service.py --live
}

case "${1:-status}" in
  install) install ;;
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  test)    test_all ;;
  logs)    docker logs -f "$NAME" ;;
  status)
    docker ps --filter "name=$NAME" --format '  container: {{.Status}}' || true
    curl -s -m 3 "http://127.0.0.1:$PORT/health" | python3 -m json.tool 2>/dev/null \
      || echo "  service not responding on :$PORT"
    ;;
  *) sed -n 2,16p "$0"; exit 1 ;;
esac
