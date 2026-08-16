"""Local demo server: single origin for `dist/` + the Route API, so one
cloudflared tunnel covers the whole app on a phone — the same shape as the old
safe-walk prototype's server/main.py.

Runs on :8020, the port SPEC.md §6.8 reserves for synthesis. This is a stopgap:
once synthesis exists and owns that port for real (REST + the alerts SSE
stream), retire this file and point cloudflared at synthesis instead.

    pip install -r server/requirements.txt
    npm run build
    uvicorn server.main:app --host 0.0.0.0 --port 8020
    cloudflared tunnel --url http://localhost:8020
"""

import json
import pathlib

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / "src/fixtures/route.json").read_text())

# harness (modules/harness) is a sibling module, not an installed package.
import sys  # noqa: E402

sys.path.insert(0, str(ROOT.parent / "harness"))

# Real routing when the graph has been built, canned fixture otherwise, so the
# demo still answers if modules/harness/scripts/build-graph.py has not run.
try:
    import harness
    from harness.routing import GRAPH_PATH

    GRAPH_OK = GRAPH_PATH.exists()
    if not GRAPH_OK:
        print(f"[map-frontend] no walk graph ({GRAPH_PATH}); serving fixture routes")
except ImportError as exc:  # noqa: BLE001 - degrade to the fixture, never fail to boot
    harness = None
    GRAPH_OK = False
    print(f"[map-frontend] harness not importable ({exc}); serving fixture routes")

app = FastAPI(title="GozAlti map-frontend demo server")

# Only needed while Vite serves the frontend on :5173. Once this serves dist/
# it is same-origin and this can go.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class RouteRequest(BaseModel):
    origin: list[float]
    dest: list[float]
    kind: str = "safer"


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/route")
def api_route(req: RouteRequest):
    if req.kind not in ("shortest", "safer"):
        return JSONResponse({"error": "unknown_kind"}, status_code=400)

    if not GRAPH_OK:
        return FIXTURE[req.kind]

    try:
        return harness.route(tuple(req.origin), tuple(req.dest), req.kind)
    except harness.RouteError as exc:
        if exc.code == "detour_cap_exceeded":
            return JSONResponse(
                {
                    "error": "detour_cap_exceeded",
                    "detour_ratio": exc.detail["detour_ratio"],
                    "cap": exc.detail["cap"],
                    "message": "No safer route within the detour cap.",
                },
                status_code=422,
            )
        return JSONResponse({"error": exc.code}, status_code=400)


DIST = ROOT / "dist"


@app.get("/tiles/{name}")
def tiles(name: str, request: Request):
    """Byte-serve the pmtiles archive.

    StaticFiles cannot do this before Starlette 0.45, and pmtiles reads the
    archive purely through Range requests — without 206 the map never draws.
    """
    if "/" in name or "\\" in name or name.startswith("."):
        return JSONResponse({"error": "bad_name"}, status_code=400)
    for base in (DIST / "tiles", ROOT / "public" / "tiles"):
        path = base / name
        if path.is_file():
            break
    else:
        return JSONResponse({"error": "not_found"}, status_code=404)

    size = path.stat().st_size
    headers = {"accept-ranges": "bytes", "cache-control": "public, max-age=3600"}
    rng = request.headers.get("range", "")

    if not rng.startswith("bytes="):
        return Response(
            path.read_bytes(), media_type="application/octet-stream", headers=headers
        )

    spec = rng[6:].split(",")[0].strip()
    first, _, last = spec.partition("-")
    if not first:  # suffix form: bytes=-500
        start, end = max(size - int(last), 0), size - 1
    else:
        start = int(first)
        end = int(last) if last else size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        return Response(status_code=416, headers={**headers, "content-range": f"bytes */{size}"})

    with path.open("rb") as fh:
        fh.seek(start)
        body = fh.read(end - start + 1)
    headers["content-range"] = f"bytes {start}-{end}/{size}"
    return Response(
        body, status_code=206, media_type="application/octet-stream", headers=headers
    )


# Serve the built frontend last so it does not shadow the API routes.
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="app")
