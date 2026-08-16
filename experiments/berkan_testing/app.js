/* GozAlti media-ingest test console.
   Everything here calls the committed module service on :8030 — this file is
   throwaway UI (gitignored); the data layer is modules/media-ingest. */

"use strict";

const API = "";                       // same origin (served by :8030)
const FOV_DEG = 60;                   // must match ingest/config.py ASSUMED_FOV_DEG
const CONE_RADIUS_M = 90;
const SEATTLE = [-122.3331, 47.6097];

let CAMS = [];                        // /api/cameras
let byId = {};
let selected = null;                  // camera_id
let pendingBearing = null;            // unconfirmed FOV rotation (deg)
let detToggle = true;
let hls = null;
let snapTimer = null;

const $ = (id) => document.getElementById(id);

/* ------------------------------------------------------------------ map */

const map = new maplibregl.Map({
  container: "map",
  center: SEATTLE,
  zoom: 13.5,
  maxPitch: 70,
  style: {
    version: 8,
    sources: {
      dark: { type: "raster", tiles: [location.origin + "/api/tile/{z}/{x}/{y}"],
              tileSize: 256, attribution: "© CARTO © OSM" },
      sat:  { type: "raster", tiles: [location.origin + "/api/sat-tile/{z}/{x}/{y}"],
              tileSize: 256, attribution: "Esri World Imagery" },
    },
    layers: [{ id: "base-dark", type: "raster", source: "dark" }],
  },
});
map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-left");

function coneFeature(cam, bearing, radius = CONE_RADIUS_M) {
  const mLat = 110574.0, mLon = 111320.0 * Math.cos(cam.lat * Math.PI / 180);
  const pts = [[cam.lon, cam.lat]];
  const n = 24;
  for (let i = 0; i <= n; i++) {
    const b = (bearing - FOV_DEG / 2 + (FOV_DEG * i) / n) * Math.PI / 180;
    pts.push([cam.lon + (radius * Math.sin(b)) / mLon,
              cam.lat + (radius * Math.cos(b)) / mLat]);
  }
  pts.push([cam.lon, cam.lat]);
  return { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [pts] } };
}

function camGeojson() {
  return {
    type: "FeatureCollection",
    features: CAMS.map((c) => ({
      type: "Feature",
      properties: {
        id: c.camera_id, stream: c.has_stream ? 1 : 0,
        selected: c.camera_id === selected ? 1 : 0,
        hasDet: c.n_detections > 0 ? 1 : 0,
        onpath: PATHCAMS.has(c.camera_id) ? 1 : 0,
        // pixel-activity tri-state: 1 active, 0 inactive, -1 unknown/stale
        act: c.active === true ? 1 : c.active === false ? 0 : -1,
      },
      geometry: { type: "Point", coordinates: [c.lon, c.lat] },
    })),
  };
}

function conesGeojson() {
  const feats = [];
  for (const c of CAMS) {
    const isSel = c.camera_id === selected;
    const bearing = isSel && pendingBearing !== null ? pendingBearing : c.bearing_deg;
    if (bearing === null || bearing === undefined) continue;
    const f = coneFeature(c, bearing);
    f.properties = {
      conf: c.bearing_conf || 0.35,
      pending: isSel && pendingBearing !== null ? 1 : 0,
      selected: isSel ? 1 : 0,
    };
    feats.push(f);
  }
  return { type: "FeatureCollection", features: feats };
}

map.on("load", () => {
  map.addSource("cones", { type: "geojson", data: conesGeojson() });
  map.addLayer({
    id: "cones-fill", type: "fill", source: "cones",
    paint: {
      "fill-color": ["case", ["==", ["get", "pending"], 1], "#F6AD55", "#4FD1C5"],
      "fill-opacity": ["*", 0.28, ["max", ["get", "conf"], 0.2]],
    },
  });
  map.addLayer({
    id: "cones-line", type: "line", source: "cones",
    paint: {
      "line-color": ["case", ["==", ["get", "pending"], 1], "#F6AD55", "#4FD1C5"],
      "line-opacity": ["case", ["==", ["get", "selected"], 1], 0.9, 0.25],
      "line-width": 1,
    },
  });

  map.addSource("radius", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "radius-fill", type: "fill", source: "radius",
    paint: { "fill-color": "#4FD1C5", "fill-opacity": 0.07 },
  });
  map.addLayer({
    id: "radius-line", type: "line", source: "radius",
    paint: { "line-color": "#4FD1C5", "line-opacity": 0.5, "line-width": 1, "line-dasharray": [3, 2] },
  });

  // location coverage polygon (convex hull of the cameras that see you)
  map.addSource("loc-hull", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "loc-hull-fill", type: "fill", source: "loc-hull",
    paint: { "fill-color": "#4FD1C5", "fill-opacity": 0.16 },
  });
  map.addLayer({
    id: "loc-hull-line", type: "line", source: "loc-hull",
    paint: { "line-color": "#4FD1C5", "line-opacity": 0.8, "line-width": 1.5 },
  });

  map.addSource("cams", { type: "geojson", data: camGeojson() });
  map.addLayer({
    id: "cams", type: "circle", source: "cams",
    paint: {
      // pixel-active cameras are bigger + brighter; inactive dim out
      "circle-radius": ["case",
        ["==", ["get", "selected"], 1], 7,
        ["==", ["get", "act"], 1], 5.5, 4.5],
      "circle-color": ["case",
        ["==", ["get", "selected"], 1], "#F6AD55",
        ["==", ["get", "act"], 1], "#6FF5E8",
        ["==", ["get", "act"], 0], "#274048",
        ["==", ["get", "stream"], 1], "#4FD1C5", "#5C6B76"],
      "circle-opacity": ["case",
        ["==", ["get", "selected"], 1], 1.0,
        ["==", ["get", "act"], 1], 1.0,
        ["==", ["get", "act"], 0], 0.45, 0.8],
      "circle-stroke-width": ["case",
        ["==", ["get", "onpath"], 1], 2.5,
        ["==", ["get", "hasDet"], 1], 2, 0.5],
      "circle-stroke-color": ["case",
        ["==", ["get", "onpath"], 1], "#E8EDF2",
        ["==", ["get", "hasDet"], 1], "#FC8181", "#0B0E11"],
    },
  });

  // refuge POIs (open businesses) — green = open now, dim = closed, amber = hours unparseable
  map.addSource("refuge", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "refuge", type: "circle", source: "refuge",
    paint: {
      "circle-radius": ["case", ["==", ["get", "open"], 1], 4, 3],
      "circle-color": ["case",
        ["==", ["get", "open"], 1], "#7CE38B",
        ["==", ["get", "open"], 0], "#39434D", "#8a6a3f"],
      "circle-opacity": ["case", ["==", ["get", "open"], 1], 0.95, 0.55],
      "circle-stroke-width": 0.5, "circle-stroke-color": "#0B0E11",
    },
  });
  map.on("click", "refuge", (e) => {
    if (gotoMode || pathMode) return;   // point-entry modes own every click
    e.originalEvent._camHit = true;
    const p = e.features[0].properties;
    const st = p.open === 1 ? `OPEN${p.until ? " until " + p.until : ""}` : p.open === 0 ? "closed" : "hours unparsed";
    new maplibregl.Popup({ closeButton: false })
      .setLngLat(e.lngLat)
      .setHTML(`<b>${p.name}</b><br>${p.kind}<br>${st}<br><span style="opacity:.6">${p.hours}</span>`)
      .addTo(map);
  });
  map.on("mouseenter", "refuge", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "refuge", () => (map.getCanvas().style.cursor = ""));

  // local-CNN detected objects rendered as simple 3D meshes (Tesla-autopilot
  // style): cars = wheels+body+cabin in bright yellow, people = body+head in
  // bright red; each mesh part is one extrusion with its own color/base/top
  map.addSource("cv-objects", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "cv-objects", type: "fill-extrusion", source: "cv-objects",
    paint: {
      "fill-extrusion-color": ["get", "color"],
      "fill-extrusion-base": ["get", "base"],
      "fill-extrusion-height": ["get", "height"],
      "fill-extrusion-opacity": 0.95,
    },
  });

  map.addSource("dets", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "dets", type: "circle", source: "dets",
    paint: {
      "circle-radius": ["case", ["==", ["get", "label"], "person"], 4.5, 5.5],
      "circle-color": ["case", ["==", ["get", "label"], "person"], "#FF4D4D", "#FFD60A"],
      "circle-opacity": 0.95,
      "circle-stroke-width": 1, "circle-stroke-color": "#0B0E11",
    },
  });

  map.on("click", "cams", (e) => {
    if (gotoMode || pathMode) return;   // point-entry modes own every click
    e.originalEvent._camHit = true;
    selectCamera(e.features[0].properties.id);
  });
  map.on("mouseenter", "cams", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "cams", () => (map.getCanvas().style.cursor = ""));

  // path layers sit under the camera dots, exits above everything
  map.addSource("path-segs", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "path-glow", type: "line", source: "path-segs",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#0B0E11", "line-width": 10, "line-opacity": 0.9 },
  }, "cams");
  map.addLayer({
    id: "path-segs", type: "line", source: "path-segs",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": ["match", ["get", "bucket"],
        "low", "#7CE38B", "medium", "#F6AD55", "high", "#FC8181", "#4FD1C5"],
      "line-width": 5,
    },
  }, "cams");
  map.addSource("path-exits", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "path-exits", type: "circle", source: "path-exits",
    paint: {
      "circle-radius": 7, "circle-color": "#7CE38B",
      "circle-stroke-width": 2, "circle-stroke-color": "#0B0E11",
    },
  });
  map.on("click", "path-exits", (e) => {
    if (gotoMode || pathMode) return;   // point-entry modes own every click
    e.originalEvent._camHit = true;
    const p = e.features[0].properties;
    new maplibregl.Popup({ closeButton: false }).setLngLat(e.lngLat)
      .setHTML(`<b>${p.name}</b><br>OPEN${p.until ? " until " + p.until : ""} · ${p.dist} m off path`)
      .addTo(map);
  });
  map.on("click", "path-segs", (e) => {
    if (gotoMode || pathMode) return;   // point-entry modes own every click
    if (e.originalEvent._camHit) return;
    e.originalEvent._segHit = true;
    const p = e.features[0].properties;
    openSeg = p.seg || null;
    openPopup = new maplibregl.Popup({ closeButton: false, maxWidth: "320px" })
      .setLngLat(e.lngLat)
      .setHTML(riskPopupHtml(p))
      .addTo(map);
    openPopup.on("close", () => { openPopup = null; openSeg = null; });
  });

  map.on("click", (e) => {
    if (e.originalEvent._camHit) return;
    if (gotoMode) { gotoClick(e.lngLat.lat, e.lngLat.lng); return; }
    if (pathMode) { pathClick(e.lngLat.lat, e.lngLat.lng); return; }
    if (e.originalEvent._segHit) return;
    clickPoint(e.lngLat.lat, e.lngLat.lng);
  });

  map.on("moveend", () => { $("btn-bldg").disabled = map.getZoom() < 14.5; });
  loadCameras();
  loadRefugeLayer();   // REF is on by default — open businesses render immediately
});

function refreshMapData() {
  if (!map.getSource("cams")) return;
  map.getSource("cams").setData(camGeojson());
  map.getSource("cones").setData(conesGeojson());
}

/* -------------------------------------------------------------- toggles */

let satOn = false, threeDOn = false;
$("btn-sat").onclick = () => {
  satOn = !satOn;
  $("btn-sat").classList.toggle("on", satOn);
  if (satOn && !map.getLayer("base-sat")) {
    map.addLayer({ id: "base-sat", type: "raster", source: "sat" }, "cones-fill");
  } else if (!satOn && map.getLayer("base-sat")) {
    map.removeLayer("base-sat");
  }
};
$("btn-3d").onclick = () => {
  threeDOn = !threeDOn;
  $("btn-3d").classList.toggle("on", threeDOn);
  map.easeTo({ pitch: threeDOn ? 58 : 0, bearing: threeDOn ? -12 : 0, duration: 700 });
};
$("btn-bldg").onclick = async () => {
  const b = map.getBounds();
  $("btn-bldg").classList.add("on");
  try {
    const gj = await fetch(`${API}/api/buildings?s=${b.getSouth().toFixed(4)}&w=${b.getWest().toFixed(4)}&n=${b.getNorth().toFixed(4)}&e=${b.getEast().toFixed(4)}`).then(r => {
      if (!r.ok) throw new Error("bbox too large or overpass down");
      return r.json();
    });
    if (map.getSource("bldg")) map.getSource("bldg").setData(gj);
    else {
      map.addSource("bldg", { type: "geojson", data: gj });
      map.addLayer({
        id: "bldg-3d", type: "fill-extrusion", source: "bldg",
        paint: {
          "fill-extrusion-color": "#1A222B",
          "fill-extrusion-height": ["get", "height"],
          "fill-extrusion-opacity": 0.85,
        },
      }, "cones-fill");
    }
    if (!threeDOn) $("btn-3d").click();
  } catch (err) {
    console.warn("buildings:", err);
    $("btn-bldg").classList.remove("on");
  }
};
$("btn-det").onclick = () => {
  detToggle = !detToggle;
  $("btn-det").classList.toggle("on", detToggle);
  if (map.getLayer("dets")) map.setLayoutProperty("dets", "visibility", detToggle ? "visible" : "none");
};

/* ---------------------------------------- local CV: 3D detected objects */

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let cvFocus = null;           // camera_id the live loop is running for
const CVOBJ = {};             // camera_id -> geojson features of its objects

function localRing(est, offsets) {
  // meter offsets [along-bearing, cross-bearing] -> lon/lat ring
  const mLat = 110574.0, mLon = 111320.0 * Math.cos(est.lat * Math.PI / 180);
  const b = ((est.bearing_deg || 0) * Math.PI) / 180;
  const ux = Math.sin(b), uy = Math.cos(b);        // along bearing (E,N)
  const vx = uy, vy = -ux;                          // perpendicular
  return offsets.map(([a, c]) => [
    est.lon + (a * ux + c * vx) / mLon,
    est.lat + (a * uy + c * vy) / mLat,
  ]);
}

function rectRing(est, cx, cy, len, wid) {
  return localRing(est, [
    [cx + len / 2, cy + wid / 2], [cx + len / 2, cy - wid / 2],
    [cx - len / 2, cy - wid / 2], [cx - len / 2, cy + wid / 2],
    [cx + len / 2, cy + wid / 2],
  ]);
}

function octRing(est, cx, cy, r) {
  const pts = [];
  for (let i = 0; i <= 8; i++) {
    const a = (2 * Math.PI * i) / 8;
    pts.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
  }
  return localRing(est, pts);
}

function meshPart(d, ring, base, height, color) {
  return {
    type: "Feature",
    properties: { label: d.label, conf: d.conf, color, base, height },
    geometry: { type: "Polygon", coordinates: [ring] },
  };
}

const CAR_LABELS = new Set(["car", "truck", "bus"]);

function objectMesh(d) {
  // composed extrusion "mesh" per detection — footprint stays the CNN's
  // estimate, only the visual shape/scale is styled here
  const [len, wid, h] = d.footprint_m;
  const est = d.est;

  if (d.label === "person") {
    // bright-red simple person: cylindrical body + head, drawn a bit larger
    // than the raw footprint (still far smaller than any car)
    const H = Math.max(h, 1.7) * 1.15;
    const bodyR = Math.max(0.5, (Math.min(len, wid) / 2) * 1.6);
    return [
      meshPart(d, octRing(est, 0, 0, bodyR), 0, H * 0.72, "#FF4D4D"),
      meshPart(d, octRing(est, 0, 0, bodyR * 0.55), H * 0.74, H, "#FF8A80"),
    ];
  }

  if (CAR_LABELS.has(d.label)) {
    // bright-yellow simple car: 4 wheels + body + cabin, oriented to bearing
    const L = len * 1.1, W = wid * 1.1, H = Math.max(h, 1.4);
    const wx = L * 0.32, wy = W / 2 - 0.12;
    const parts = [];
    for (const [ax, cy] of [[wx, wy], [wx, -wy], [-wx, wy], [-wx, -wy]])
      parts.push(meshPart(d, rectRing(est, ax, cy, 0.7, 0.3), 0, H * 0.3, "#15181C"));
    parts.push(meshPart(d, rectRing(est, 0, 0, L, W), H * 0.12, H * 0.58, "#FFD60A"));
    parts.push(meshPart(d, rectRing(est, -L * 0.06, 0, L * 0.45, W * 0.8), H * 0.58, H * 1.02, "#F2C200"));
    return parts;
  }

  // everything else keeps the plain box in the old palette
  const color = d.label === "bicycle" || d.label === "motorbike" ? "#F6AD55" : "#4FD1C5";
  return [meshPart(d, rectRing(est, 0, 0, len, wid), 0, h, color)];
}

function renderCvObjects() {
  if (!map.getSource("cv-objects")) return;
  const feats = Object.values(CVOBJ).flat();
  map.getSource("cv-objects").setData({ type: "FeatureCollection", features: feats });
}

function ingestCvResult(res) {
  if (!res || !res.ok) return 0;
  const feats = [];
  let placed = 0;
  for (const d of res.detections || []) {
    if (!d.est || !d.footprint_m) continue;
    feats.push(...objectMesh(d));
    placed++;
  }
  CVOBJ[res.camera_id] = feats;
  renderCvObjects();
  return placed;
}

function cvPanel(res) {
  const el = $("c-cv");
  if (!res) { el.textContent = "focus a camera to start the live loop"; return; }
  if (!res.ok) { el.textContent = res.why || "cv unavailable"; el.classList.add("dim"); return; }
  el.classList.remove("dim");
  const placed = (res.detections || []).filter((d) => d.est);
  const rows = placed.slice(0, 8).map((d) =>
    `<div>${d.label} ${(d.conf * 100) | 0}% @ ${d.est.range_m} m brg ${Math.round(d.est.bearing_deg)}°` +
    `${d.est.bearing_basis === "axis-only-unresolved" ? " <span style='opacity:.5'>(bearing unresolved)</span>" : ""}</div>`);
  el.innerHTML =
    `<div class="dim">${res.model} · ${res.took_ms} ms${res.cached ? " · cached frame" : ""} · frame ${res.frame_ts}</div>` +
    (rows.join("") || "<div>nothing detected in view</div>");
}

async function cvLoop(cid) {
  // low-latency mode: poll fast — the server prefetches hot cameras in the
  // background, so these calls answer from cache in ms and NEVER trigger
  // extra upstream fetches (frame gates + per-frame result cache own that).
  let tilted = false;
  let lastFrame = null;
  while (cvFocus === cid) {
    try {
      const res = await fetch(`${API}/api/cv/camera/${cid}`).then((r) => r.json());
      if (cvFocus !== cid) break;
      if (res.frame_ts !== lastFrame) {       // new frame -> new scene
        lastFrame = res.frame_ts;
        const n = ingestCvResult(res);
        cvPanel(res);
        if (n > 0 && !tilted && !threeDOn) { $("btn-3d").click(); tilted = true; }
      }
    } catch (_) {
      await sleep(2000);   // service hiccup — back off a little extra
    }
    await sleep(350);
  }
}

$("btn-hq").onclick = async () => {
  if (!selected) return;
  const btn = $("btn-hq");
  btn.disabled = true;
  btn.textContent = "HQ RUNNING (detlib)…";
  try {
    const res = await fetch(`${API}/api/cv/camera/${selected}?backend=detlib&force=true`)
      .then((r) => r.json());
    ingestCvResult(res);
    cvPanel(res);
  } catch (_) {}
  btn.disabled = false;
  btn.textContent = "HQ PASS — ADI'S DETLIB";
};

function startCvFocus(cid) {
  const was = cvFocus;
  cvFocus = cid;
  if (was !== cid) cvLoop(cid);
}

/* ----------------------------------------------------- refuge map layer */

let refOn = true;
let refTimer = null;

async function loadRefugeLayer() {
  if (!refOn || map.getZoom() < 12.5) return;
  const b = map.getBounds();
  try {
    const pois = await fetch(`${API}/api/refuge/bbox?s=${b.getSouth()}&w=${b.getWest()}&n=${b.getNorth()}&e=${b.getEast()}`).then((r) => r.json());
    map.getSource("refuge").setData({
      type: "FeatureCollection",
      features: pois.map((p) => ({
        type: "Feature",
        properties: {
          name: p.name, kind: p.kind, hours: p.opening_hours,
          open: p.open_now === true ? 1 : p.open_now === false ? 0 : -1,
          until: p.open_until || "",
        },
        geometry: { type: "Point", coordinates: [p.lon, p.lat] },
      })),
    });
  } catch (_) { /* dataset not built yet */ }
}

$("btn-ref").onclick = () => {
  refOn = !refOn;
  $("btn-ref").classList.toggle("on", refOn);
  if (map.getLayer("refuge"))
    map.setLayoutProperty("refuge", "visibility", refOn ? "visible" : "none");
  if (refOn) loadRefugeLayer();
};
map.on("moveend", () => {
  if (!refOn) return;
  clearTimeout(refTimer);
  refTimer = setTimeout(loadRefugeLayer, 400);
});

function renderRefuge(scope, res) {
  $("refuge-scope").textContent = scope;
  if (!res || !res.available) {
    $("refuge-head").textContent = (res && res.why) || "refuge dataset not built";
    $("refuge-list").innerHTML = "";
    return;
  }
  const nearest = res.nearest_open
    ? ` · nearest open: ${res.nearest_open.name} (${Math.round(res.nearest_open.dist_m)} m${res.nearest_open.open_until ? ", until " + res.nearest_open.open_until : ""})`
    : " · none open now";
  $("refuge-head").textContent =
    `${res.n_known_hours} with known hours · ${res.n_open_now} open now${nearest}`;
  $("refuge-list").innerHTML = (res.pois || []).slice(0, 12).map((p) => {
    const cls = p.open_now === true ? "open" : p.open_now === false ? "closed" : "unknown";
    const st = p.open_now === true ? `OPEN${p.open_until ? "→" + p.open_until : ""}`
      : p.open_now === false ? "closed" : "hours?";
    return `<div class="poi-row"><span class="nm">${p.name} <span style="opacity:.5">${Math.round(p.dist_m)}m</span></span><span class="st ${cls}">${st}</span></div>`;
  }).join("");
}

/* ------------------------------------------- evidence-weighted pathfinding */

let pathMode = false;             // two-click A→B entry; auto-disables on entry
let gotoMode = false;             // one-click "take me to" from current location
let pathA = null;                 // [lat, lon] of the first click
let pathMarkers = [];
const PATHCAMS = new Set();       // camera_ids highlighted as en-route
const PATHCV = new Map();         // cid -> freshest ok CV result on the corridor
let pathCvToken = 0;              // invalidates a stale en-route CV watch
let PATHID = null;                // live path id — keys the LLM summaries
let SUMS = null;                  // last /api/path/summaries/{id} response
let sumTimer = null;
let openPopup = null, openSeg = null;  // segment popup that live-updates

function dropMarker(lat, lon, color) {
  const el = document.createElement("div");
  el.style.cssText = `width:16px;height:16px;border-radius:50%;background:${color};` +
    "border:2px solid #0B0E11;box-shadow:0 0 0 3px rgba(232,237,242,0.25)";
  const m = new maplibregl.Marker({ element: el }).setLngLat([lon, lat]).addTo(map);
  pathMarkers.push(m);
  return m;
}

function clearPath() {
  pathA = null;
  pathCvToken++;                  // cancel the en-route CV watch
  PATHCV.clear();
  PATHID = null;
  SUMS = null;
  clearInterval(sumTimer);
  if (openPopup) openPopup.remove();
  pathMarkers.forEach((m) => m.remove());
  pathMarkers = [];
  PATHCAMS.clear();
  if (map.getSource("path-segs"))
    map.getSource("path-segs").setData({ type: "FeatureCollection", features: [] });
  if (map.getSource("path-exits"))
    map.getSource("path-exits").setData({ type: "FeatureCollection", features: [] });
  refreshMapData();
}

async function pathClick(lat, lon) {
  if (pathA === null) {
    clearPath();
    pathA = [lat, lon];
    dropMarker(lat, lon, "#F6AD55");           // A
    $("cambar").innerHTML = `<span class="cambar-note">A set — click destination…</span>`;
    return;
  }
  const [alat, alon] = pathA;
  pathA = null;
  dropMarker(lat, lon, "#4FD1C5");             // B
  setPathMode(false);   // path fully entered → mode disables itself, route stays
  await requestRoute(alat, alon, lat, lon);
}

async function requestRoute(alat, alon, blat, blon) {
  $("cambar").innerHTML = `<span class="cambar-note">routing…</span>`;
  let r;
  try {
    // modules/pathfinding: one-and-done + auto-started PathLiveSession
    const resp = await fetch(`${API}/api/route?olat=${alat}&olon=${alon}&dlat=${blat}&dlon=${blon}&kind=safer`);
    r = await resp.json();
    if (!resp.ok) throw new Error((r.detail && r.detail.error) || "route failed");
  } catch (err) {
    $("cambar").innerHTML = `<span class="cambar-note">no route: ${err.message} — press PATH or TAKE ME TO to retry</span>`;
    return false;
  }
  renderPath(r);
  watchLivePath(r.path_id, r.version);
  return true;
}

/* poll the PathLiveSession — the path AUTO-REPLACES when live data (fresh
   opencv, VLM observations, SDOT/osint backfills) changes the optimum */
let liveWatch = null;

function watchLivePath(pathId, version) {
  if (liveWatch) clearInterval(liveWatch);
  let v = version;
  liveWatch = setInterval(async () => {
    try {
      const res = await fetch(`${API}/api/route/live/${pathId}?since=${v}`);
      if (res.status === 404) { clearInterval(liveWatch); return; }
      const r = await res.json();
      if (r.version > v) {
        v = r.version;
        renderPath(r.path);     // auto-replace, per the design decision
      }
    } catch (_) {}
  }, 2000);
}

/* Deterministic factor breakdown for one segment — mirrors RISK_FORMULA in
   modules/media-ingest/ingest/pathrisk.py exactly (every delta below is the
   server's own term, applied to the server's own evidence; nothing invented). */
function riskFactors(s) {
  if (s.risk_parts) {
    // consolidated-router shape: risk_parts IS the real breakdown — every
    // named part is already weighted; render them verbatim, largest first
    const SRC = { traffic: "OSM road class", lighting: "lit tag/prior (night-scaled)",
                  sidewalk: "OSM sidewalk tags", collisions: "REAL SDOT ped/cyclist density",
                  coverage: "camera-coverage gap", osint: "osint AreaSignals",
                  crossings: "junction density", occupancy: "LIVE night-rule (people on cameras)",
                  vlm_flags: "LIVE VLM flags" };
    const rows = Object.entries(s.risk_parts)
      .filter(([, v]) => v > 0.001)
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => ({ label: `${k} — ${SRC[k] || k}`, delta: v }));
    const info = s.cameras && s.cameras.length
      ? [`covered by: ${s.cameras.join(", ")}`] : ["no camera covers this segment"];
    return { rows, info };
  }
  const e = s.evidence;
  const rows = [{ label: "base structural risk (harness A* weights)", delta: s.base_risk, base: true }];
  if (e.night_unlit_penalty) rows.push({ label: "night + street not tagged lit", delta: 0.15 });
  else if (!e.daylight) rows.push({ label: `night, but street tagged lit: ${e.lit}`, delta: 0 });
  const nCams = e.cameras_80m.length;
  if (!nCams) rows.push({ label: "no camera within 80 m", delta: 0.10 });
  else {
    rows.push({ label: `covered by ${nCams} camera(s) ≤80 m`, delta: -0.05 });
    if (e.cameras_active > 0)
      rows.push({ label: `${e.cameras_active} of them pixel-active right now`, delta: -0.05 });
  }
  const open = e.open_refuges_120m;
  if (open) {
    const nm = e.nearest_open ? ` — nearest: ${e.nearest_open.name}` : "";
    rows.push({ label: `${open} business(es) open now ≤120 m${nm}`, delta: -(0.10 * Math.min(open, 3) / 3) });
  } else rows.push({ label: "no business open within 120 m", delta: 0.10 });
  const info = [];
  if (e.last_person_min != null) info.push(`last person seen on-camera ${e.last_person_min} min ago`);
  if (e.sidewalk) info.push(`sidewalk: ${e.sidewalk}`);
  if (e.alley_dist_m != null) info.push(`nearest alley: ${e.alley_dist_m} m`);
  return { rows, info };
}

function riskPopupHtml(p) {
  // p comes from the geojson feature; nested objects arrive JSON-stringified
  const rows = JSON.parse(p.factors || "[]");
  const info = JSON.parse(p.info || "[]");
  const fmt = (d) => (d > 0 ? `+${d.toFixed(2)}` : d < 0 ? `−${Math.abs(d).toFixed(2)}` : "±0");
  const cls = (r) => (r.base ? "rp-base" : r.delta > 0 ? "rp-up" : r.delta < 0 ? "rp-down" : "rp-zero");
  return `<div class="risk-pop">
    <div class="rp-head"><b>${p.name}</b><span class="rp-bucket ${p.bucket}">${p.bucket}</span></div>
    ${rows.map((r) => `<div class="rp-row ${cls(r)}"><span>${r.label}</span><b>${fmt(r.delta)}</b></div>`).join("")}
    <div class="rp-total"><span>live risk (clamped 0–1)</span><b>${p.risk}</b></div>
    ${info.length ? `<div class="rp-info">${info.join(" · ")}</div>` : ""}
    ${p.seg ? `<div class="rp-llm">${llmLine(p.seg)}</div>` : ""}
    <div class="rp-note">deterministic evidence combination — not a safety verdict</div>
  </div>`;
}

/* ---------------- per-segment LLM summaries (Ollama ON the DGX Spark) ----
   The pathfind live session queues one summary per segment as deterministic
   + opencv evidence arrives and re-queues whichever segments' evidence
   moves; we just poll and live-update the open popup. The text is labeled
   LLM phrasing of that evidence — the model adds no facts of its own. */

function llmLine(seg) {
  const s = SUMS && SUMS.summaries && SUMS.summaries[seg];
  if (s && s.text) {
    return `${s.text}<div class="rp-llm-meta">${s.model} on the Spark` +
      `${s.revising ? " · revising with fresh evidence…" : ""}</div>`;
  }
  if (SUMS && SUMS.available === false)
    return `<span class="dim">LLM summary unavailable — ${SUMS.why || "backend offline"}</span>`;
  if (s && s.error)
    return `<span class="dim">LLM summary failed: ${s.error}</span>`;
  return `<span class="dim">LLM summary — generating on the Spark…</span>`;
}

function refreshOpenPopup() {
  if (!openPopup || !openSeg) return;
  const el = openPopup.getElement() && openPopup.getElement().querySelector(".rp-llm");
  if (el) el.innerHTML = llmLine(openSeg);
}

function startSummaryPoll() {
  clearInterval(sumTimer);
  sumTimer = setInterval(async () => {
    if (!PATHID) return;
    try {
      SUMS = await fetch(`${API}/api/path/summaries/${PATHID}`).then((r) => r.json());
      refreshOpenPopup();
    } catch (_) { /* service restarting */ }
  }, 3000);
}

/* Corridor object watch — rides the SAME machinery the pathfind live
   session uses, instead of forcing inference from the client:

   - the PathLiveSession (auto-started by /api/route) marks every en-route
     camera hot each ~4 s tick; cvdetect's prefetcher does ALL fetching +
     CNN inference at its proven rate-gated cadence, and the fresh corridor
     results ship inside every path version's cv_detections (renderPath
     ingests those and fills PATHCV).
   - between version bumps this loop READS the per-frame result cache with
     plain /api/cv/camera/{cid} — answered in milliseconds, zero upstream
     requests while a camera's frame gate is shut.
   - stream-capable cameras are swept ONLY while focused: the endpoint's
     stream.ensure(evict=true) would churn the STREAM_MAX streamer slots if
     a whole corridor went through it (the prefetcher itself uses
     evict=false for exactly this reason). Their boxes still refresh via
     cv_detections on every version bump, and live while focused.

   The old one-shot forced-detlib still pass lived here; it bypassed every
   cache and gate, blocked seconds per camera, and was stale the moment it
   finished. The HQ button keeps that power for the focused camera as a
   deliberate user action. */
async function watchPathCv() {
  const token = ++pathCvToken;
  const note = document.createElement("span");
  note.className = "cambar-note";
  const VEH = ["car", "truck", "bus", "motorbike", "bicycle"];
  const lastFrame = new Map();          // cid -> frame_ts already drawn
  while (pathCvToken === token && PATHID) {
    // corridor membership is re-read every sweep — auto-replaced paths
    // swap PATHCAMS underneath us and the watch just follows
    const cids = [...PATHCAMS].filter((cid) => {
      const rec = byId[cid];
      return cid === cvFocus || !rec || !rec.has_stream;
    });
    let awaiting = 0;
    const queue = [...cids];
    const workers = Array.from({ length: Math.min(4, Math.max(queue.length, 1)) }, async () => {
      while (queue.length && pathCvToken === token) {
        const cid = queue.shift();
        try {
          const res = await fetch(`${API}/api/cv/camera/${encodeURIComponent(cid)}`)
            .then((r) => r.json());
          if (pathCvToken !== token) return;
          if (res.ok) {
            PATHCV.set(cid, res);
            if (res.frame_ts !== lastFrame.get(cid)) {   // new frame -> new scene
              lastFrame.set(cid, res.frame_ts);
              ingestCvResult(res);
            }
          } else awaiting++;
        } catch (_) { awaiting++; }
      }
    });
    await Promise.all(workers);
    if (pathCvToken !== token) return;
    for (const cid of [...PATHCV.keys()])
      if (!PATHCAMS.has(cid)) PATHCV.delete(cid);        // corridor moved on
    let people = 0, vehicles = 0;
    for (const res of PATHCV.values())
      for (const d of res.detections || []) {
        if (d.label === "person") people++;
        else if (VEH.includes(d.label)) vehicles++;
      }
    // fillCambar wipes the bar on every re-render; re-attach quietly
    if (!note.isConnected) $("cambar").appendChild(note);
    note.textContent = `on-path CV (hot-lane cache): ${people} people · ${vehicles} vehicles` +
      ` across ${PATHCV.size}/${PATHCAMS.size} cam(s)` +
      (awaiting ? ` · ${awaiting} awaiting first read` : "");
    await sleep(3500);   // cache reads only — the prefetcher owns the real work
  }
}

function renderPath(r) {
  // LLM summaries: the server session queues them per segment; poll by path id
  if (r.path_id && r.path_id !== PATHID) {
    PATHID = r.path_id;
    SUMS = null;
    startSummaryPoll();
  }
  // segments colored by live risk; shortest-kind (no segments) draws neutral
  const segFeats = (r.segments && r.segments.length)
    ? r.segments.map((s) => {
        const { rows, info } = riskFactors(s);
        return {
          type: "Feature",
          properties: { bucket: s.risk_bucket, name: s.name, risk: s.live_risk,
                        base: s.base_risk, factors: JSON.stringify(rows),
                        info: JSON.stringify(info) },
          geometry: s.geometry,
        };
      })
    : [{ type: "Feature", properties: { bucket: "", name: "route", risk: "", base: "", factors: "[]", info: "[]" },
         geometry: { type: "LineString", coordinates: r.polyline.map(([la, lo]) => [lo, la]) } }];
  map.getSource("path-segs").setData({ type: "FeatureCollection", features: segFeats });

  // open "exit route" businesses along the walk
  map.getSource("path-exits").setData({
    type: "FeatureCollection",
    features: (r.refuges_en_route || []).map((p) => ({
      type: "Feature",
      properties: { name: p.name, until: p.open_until || "", dist: Math.round(p.dist_m) },
      geometry: { type: "Point", coordinates: [p.lon, p.lat] },
    })),
  });

  // en-route cameras: highlight on map + chips bar
  PATHCAMS.clear();
  (r.cameras_en_route || []).forEach((cid) => PATHCAMS.add(cid));
  refreshMapData();
  const camRecords = (r.cameras_en_route_detail || []).map((c) => byId[c.camera_id] || c);
  fillCambar(camRecords, `path · ${r.length_m} m · ~${Math.round(r.eta_min)} min`);

  // refuge panel becomes the exit-route list for this walk
  const exits = r.refuges_en_route || [];
  renderRefuge(`open exits along path (${exits.length})`, {
    available: true, n_known_hours: exits.length, n_open_now: exits.length,
    n_hours_unparsed: 0, nearest_open: exits[0] || null, pois: exits,
  });

  const buckets = { low: 0, medium: 0, high: 0 };
  (r.segments || []).forEach((s) => buckets[s.risk_bucket]++);
  // live-session status badge: honest about which layers are in the route
  const L = r.live;
  const liveBadge = L
    ? (L.incorporated
        ? `<span style="color:#7CE38B">LIVE v${r.version} ✓ ${(L.layers_incorporated || []).join("+")}</span>`
        : `<span style="color:#F6AD55">LIVE PENDING — ${L.basis}</span>`)
    : "";
  $("cambar").insertAdjacentHTML("afterbegin",
    `<span class="cambar-note" title="${r.risk_basis}">` +
    `<span style="color:#7CE38B">■${buckets.low}</span> ` +
    `<span style="color:#F6AD55">■${buckets.medium}</span> ` +
    `<span style="color:#FC8181">■${buckets.high}</span> · ` +
    `${r.evidence_summary} · ${r.daylight ? "daylight" : "night"} · ${liveBadge} · click a segment for the factor breakdown</span>`);

  // CV shipped with the path object renders immediately (cached, honest
  // age) — the live session refreshes these on every version bump, so this
  // is the corridor's primary object feed, including streamer cameras
  Object.values(r.cv_detections || {}).forEach((res) => {
    PATHCV.set(res.camera_id, res);
    ingestCvResult(res);
  });

  // one corridor watch per NEW path: continuous hot-lane cache reads
  // (replaced the old one-shot forced-detlib still pass)
  if (r.path_id !== lastCvWatchPathId) {
    lastCvWatchPathId = r.path_id;
    watchPathCv();
  }
}
let lastCvWatchPathId = null;

function setPathMode(on) {
  pathMode = on;
  $("btn-path").classList.toggle("on", on);
  if (on) {
    setGotoMode(false);
    clearPath();       // entering the mode starts a fresh path
    $("cambar").innerHTML = `<span class="cambar-note">PATH mode — click your start point (A)</span>`;
  }
  // turning off (auto or manual) keeps whatever route is drawn
}

function setGotoMode(on) {
  gotoMode = on;
  $("btn-goto").classList.toggle("on", on);
  if (on) setPathMode(false);
}

$("btn-path").onclick = () => setPathMode(!pathMode);

$("btn-goto").onclick = async () => {
  if (gotoMode) { setGotoMode(false); return; }
  setGotoMode(true);
  if (!lastLoc) {
    $("cambar").innerHTML = `<span class="cambar-note">TAKE ME TO — getting your location first…</span>`;
    await enableLocation();          // resolves only when fully loaded
  }
  if (gotoMode && lastLoc)
    $("cambar").innerHTML = `<span class="cambar-note">TAKE ME TO — location locked, click your destination</span>`;
};

async function gotoClick(lat, lon) {
  setGotoMode(false);  // destination entered → mode disables itself
  if (!lastLoc) {
    // destination clicked while the fix is still loading — wait on the SAME
    // in-flight request (enableLocation is single-flight), then route
    $("cambar").innerHTML = `<span class="cambar-note">destination set — waiting for your location…</span>`;
    await enableLocation();
  }
  if (!lastLoc) {
    $("cambar").innerHTML = `<span class="cambar-note">could not get a location — press LOC, then retry</span>`;
    return;
  }
  clearPath();
  dropMarker(lastLoc[0], lastLoc[1], "#F6AD55");   // A = you
  dropMarker(lat, lon, "#4FD1C5");                 // B = destination
  await requestRoute(lastLoc[0], lastLoc[1], lat, lon);
}

/* --------------------------------------------- my location -> coverage */

let locMarker = null;
let lastLoc = null;               // [lat, lon] once location is enabled

function convexHull(pts) {
  // Andrew monotone chain on [lon, lat]; fine at city scale
  const p = [...pts].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (p.length < 3) return null;
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower = [], upper = [];
  for (const q of p) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], q) <= 0) lower.pop();
    lower.push(q);
  }
  for (const q of p.reverse()) {
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], q) <= 0) upper.pop();
    upper.push(q);
  }
  const hull = lower.slice(0, -1).concat(upper.slice(0, -1));
  return hull.length >= 3 ? [...hull, hull[0]] : null;
}

async function showCoverage(lat, lon) {
  lastLoc = [lat, lon];
  map.flyTo({ center: [lon, lat], zoom: 15.5 });
  if (locMarker) locMarker.remove();
  const el = document.createElement("div");
  el.style.cssText = "width:14px;height:14px;border-radius:50%;background:#F6AD55;" +
    "border:2px solid #0B0E11;box-shadow:0 0 0 4px rgba(246,173,85,0.35)";
  locMarker = new maplibregl.Marker({ element: el }).setLngLat([lon, lat]).addTo(map);

  const radius = 250;
  const res = await fetch(`${API}/api/nearby?lat=${lat}&lon=${lon}&radius_m=${radius}`).then((r) => r.json());
  const cams = res.cameras;
  fillCambar(cams, "your position");

  // the region these cameras form: convex hull of camera positions + you
  const hullPts = cams.map((c) => [c.lon, c.lat]).concat([[lon, lat]]);
  const ring = convexHull(hullPts);
  map.getSource("loc-hull").setData(ring
    ? { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring] } }] }
    : { type: "FeatureCollection", features: [] });
  map.getSource("radius").setData({ type: "FeatureCollection", features: [circleFeature(lat, lon, radius)] });
  if (cams.length) selectCamera(cams[0].camera_id);
}

let locPromise = null;            // single in-flight location request

function enableLocation() {
  // Resolves only once the location is FULLY loaded (fix acquired AND
  // showCoverage finished — geolocation, or map-center fallback for
  // plain-HTTP testing where the browser blocks it). Concurrent callers
  // (TAKE ME TO button + an early destination click) share one request.
  if (locPromise) return locPromise;
  locPromise = new Promise((resolve) => {
    const done = (lat, lon) =>
      showCoverage(lat, lon).then(() => resolve(lastLoc), () => resolve(lastLoc));
    if (!navigator.geolocation) {
      const c = map.getCenter();
      done(c.lat, c.lng);
      return;
    }
    $("btn-loc").classList.add("on");
    navigator.geolocation.getCurrentPosition(
      (pos) => done(pos.coords.latitude, pos.coords.longitude),
      (err) => {
        // geolocation needs localhost or https; fall back to map center for testing
        console.warn("geolocation failed:", err.message, "— using map center");
        const c = map.getCenter();
        done(c.lat, c.lng);
      },
      { enableHighAccuracy: true, timeout: 8000 },
    );
  }).finally(() => { locPromise = null; });
  return locPromise;
}

$("btn-loc").onclick = () => { enableLocation(); };

/* ---------------------------------------------------------- rail toggles */

$("rail-collapse").onclick = () => {
  document.body.classList.add("left-collapsed");
  $("rail-expand").style.display = "";
  setTimeout(() => map.resize(), 220);
};
$("rail-expand").onclick = () => {
  document.body.classList.remove("left-collapsed");
  $("rail-expand").style.display = "none";
  setTimeout(() => map.resize(), 220);
};
$("tb-streets").onclick = () => {
  document.body.classList.toggle("drawer-left");
  document.body.classList.remove("drawer-right");
};
$("tb-panel").onclick = () => {
  document.body.classList.toggle("drawer-right");
  document.body.classList.remove("drawer-left");
};
map.on("click", () => document.body.classList.remove("drawer-left", "drawer-right"));

/* ------------------------------------------------------------- data load */

async function loadCameras() {
  CAMS = await fetch(`${API}/api/cameras`).then((r) => r.json());
  byId = Object.fromEntries(CAMS.map((c) => [c.camera_id, c]));
  refreshMapData();
}

async function loadStreets() {
  const streets = await fetch(`${API}/api/streets`).then((r) => r.json());
  const list = $("street-list");
  list.innerHTML = "";
  for (const s of streets.sort((a, b) => b.cameras - a.cameras)) {
    const div = document.createElement("div");
    div.className = "street-item";
    div.dataset.name = s.name.toLowerCase();
    div.innerHTML = `<span class="name">${s.name}</span><span class="meta">${s.cameras} cam</span>`;
    div.onclick = () => selectStreet(s.name, div);
    list.appendChild(div);
  }
}
$("street-search").oninput = (e) => {
  const q = e.target.value.toLowerCase();
  for (const el of document.querySelectorAll(".street-item"))
    el.style.display = el.dataset.name.includes(q) ? "" : "none";
};

/* ------------------------------------------------- click -> convergence */

function circleFeature(lat, lon, radius) {
  const mLat = 110574.0, mLon = 111320.0 * Math.cos(lat * Math.PI / 180);
  const pts = [];
  for (let i = 0; i <= 48; i++) {
    const a = (2 * Math.PI * i) / 48;
    pts.push([lon + (radius * Math.sin(a)) / mLon, lat + (radius * Math.cos(a)) / mLat]);
  }
  return { type: "Feature", geometry: { type: "Polygon", coordinates: [pts] } };
}

async function clickPoint(lat, lon) {
  const radius = 100;
  map.getSource("radius").setData({ type: "FeatureCollection", features: [circleFeature(lat, lon, radius)] });
  const res = await fetch(`${API}/api/nearby?lat=${lat}&lon=${lon}&radius_m=${radius}`).then((r) => r.json());
  let cams = res.cameras;
  if (!cams.length) {
    // widen once so a miss still routes you somewhere useful
    const wide = await fetch(`${API}/api/nearby?lat=${lat}&lon=${lon}&radius_m=400`).then((r) => r.json());
    cams = wide.cameras;
    map.getSource("radius").setData({ type: "FeatureCollection", features: [circleFeature(lat, lon, 400)] });
  }
  fillCambar(cams, res.street_near ? `near ${res.street_near}` : "");
  if (cams.length) {
    selectCamera(cams[0].camera_id);
    // one-shot multi-camera pass: objects from EVERY camera that sees this
    // spot UPSERT into the scene — existing rendered cars/people persist
    // (each camera's boxes are replaced only when IT produces a new result)
    fetch(`${API}/api/cv/point?lat=${lat}&lon=${lon}&radius_m=${Math.max(150, radius)}`)
      .then((r) => r.json())
      .then((pt) => (pt.cameras || []).forEach(ingestCvResult))
      .catch(() => {});
  }
}

async function selectStreet(name, el) {
  document.querySelectorAll(".street-item.active").forEach((x) => x.classList.remove("active"));
  if (el) el.classList.add("active");
  document.body.classList.remove("drawer-left", "drawer-right");
  const cams = await fetch(`${API}/api/street/${encodeURIComponent(name)}`).then((r) => r.json());
  fillCambar(cams, name);
  streetRefugeHold = true;
  fetch(`${API}/api/refuge/street/${encodeURIComponent(name)}`)
    .then((r) => r.json())
    .then((res) => renderRefuge(`along ${name} (${res.cameras_considered || 0} cameras)`, res))
    .catch(() => {});
  const lons = cams.map((c) => c.lon), lats = cams.map((c) => c.lat);
  map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
                { padding: 80, maxZoom: 16 });
  if (cams.length) selectCamera(cams[0].camera_id);
}

function fillCambar(cams, label) {
  const bar = $("cambar");
  bar.innerHTML = "";
  for (const c of cams) {
    const chip = document.createElement("div");
    chip.className = "cam-chip";
    chip.dataset.id = c.camera_id;
    const d = c.dist_m !== undefined && c.dist_m !== null ? ` · ${Math.round(c.dist_m)}m` : "";
    chip.innerHTML = `${c.desc || c.camera_id}<span class="tag ${c.has_stream ? "live" : ""}">${c.has_stream ? "LIVE" : "SNAP"}${d}</span>`;
    chip.onclick = () => selectCamera(c.camera_id);
    bar.appendChild(chip);
  }
}

/* -------------------------------------------------------- camera select */

function selectCamera(cid) {
  selected = cid;
  pendingBearing = null;
  const c = byId[cid];
  if (!c) return;
  document.querySelectorAll(".cam-chip").forEach((x) =>
    x.classList.toggle("active", x.dataset.id === cid));
  refreshMapData();
  openFeed(c);
  fillCamPanel(c);
  refreshCamDetail(cid);
  startCvFocus(cid);   // single-camera focus -> live local-CV loop
}

function openFeed(c) {
  const vp = $("viewport");
  vp.innerHTML = "";
  if (hls) { hls.destroy(); hls = null; }
  if (snapTimer) { clearInterval(snapTimer); snapTimer = null; }

  const pane = document.createElement("div");
  pane.className = "pane";
  const label = document.createElement("div");
  label.className = "pane-label";
  label.innerHTML = `<span class="badge ${c.has_stream ? "live" : "snap"}">${c.has_stream ? "LIVE" : "SNAP"}</span><span class="desc">${c.desc || c.camera_id}</span>`;
  pane.appendChild(label);
  const ts = document.createElement("div");
  ts.className = "pane-ts";
  ts.textContent = new Date().toLocaleTimeString();
  pane.appendChild(ts);

  if (c.has_stream && window.Hls && Hls.isSupported()) {
    const video = document.createElement("video");
    video.muted = true; video.autoplay = true; video.playsInline = true;
    pane.appendChild(video);
    hls = new Hls({ liveDurationInfinity: true });
    hls.loadSource(`${API}${c.hls}`);
    hls.attachMedia(video);
    hls.on(Hls.Events.ERROR, (_e, data) => {
      if (data.fatal) { hls.destroy(); hls = null; video.remove(); snapshotInto(pane, c, ts); }
    });
  } else {
    snapshotInto(pane, c, ts);
  }
  vp.appendChild(pane);
  overlayFrameDetections(pane, c.camera_id);
}

function snapshotInto(pane, c, ts) {
  const img = document.createElement("img");
  img.className = "snap";
  const load = () => { img.src = `${API}${c.snapshot}?t=${Date.now()}`; ts.textContent = new Date().toLocaleTimeString(); };
  load();
  snapTimer = setInterval(load, 60000);   // snapshot cadence — matches upstream discipline
  pane.appendChild(img);
}

async function overlayFrameDetections(pane, cid) {
  try {
    const d = await fetch(`${API}/api/detections/${cid}`).then((r) => r.json());
    if (!d || !d.ok || !d.detections) return;
    for (const det of d.detections) {
      const dot = document.createElement("div");
      dot.className = `det-box ${det.label === "person" ? "person" : ""}`;
      dot.style.left = `${det.cx * 100}%`;
      dot.style.top = `${det.cy * 100}%`;
      dot.title = `${det.label} ${(det.conf * 100) | 0}%`;
      pane.appendChild(dot);
    }
  } catch (_) { /* no detections yet */ }
}

/* ----------------------------------------------------- right rail panel */

function fillCamPanel(c) {
  $("cam-empty").style.display = "none";
  $("cam-panel").style.display = "";
  $("c-id").textContent = c.camera_id;
  $("c-desc").textContent = c.desc || "—";
  $("c-street").textContent = c.street || "not snapped";
  $("c-mode").textContent = c.has_stream ? "HLS live" : "snapshot";
  renderActivityRow(c);
  renderBearingRow(c);
  const sat = $("sat-thumb");
  sat.style.display = "";
  sat.src = `${API}/api/satellite/${c.camera_id}`;
  sat.onerror = () => (sat.style.display = "none");
}

function renderBearingRow(c) {
  const shown = pendingBearing !== null ? pendingBearing : c.bearing_deg;
  $("c-bearing").textContent = shown === null || shown === undefined
    ? "unresolved" : `${Math.round(shown)}°${pendingBearing !== null ? " (pending)" : ""}`;
  $("c-bearing").className = pendingBearing !== null ? "warn" : (c.bearing_deg != null ? "ok" : "warn");
  $("c-conf").textContent = c.bearing_conf != null ? c.bearing_conf : "—";
  $("c-basis").textContent = c.bearing_basis || "—";
}

function agoText(iso) {
  if (!iso) return null;
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (isNaN(s)) return iso;
  if (s < 90) return `${Math.round(s)}s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  return `${(s / 3600).toFixed(1)} h ago`;
}

async function refreshCamDetail(cid) {
  const ctx = await fetch(`${API}/api/context/${cid}`).then((r) => r.json());
  const layers = (ctx.bearing && ctx.bearing.layers) || [];
  $("c-layers").innerHTML = layers.length
    ? layers.map((l) => `<div>${l.ok ? "✓" : "✗"} ${l.layer}${l.why ? " — " + l.why : ""}${l.bearing_deg !== undefined ? " → " + Math.round(l.bearing_deg) + "°" : ""}</div>`).join("")
    : "no layers run yet — RUN AUTO LAYERS or confirm manually";
  renderDetections(ctx.detections);

  // co-presence — evidence-attached, or honestly absent
  const cop = ctx.copresence;
  $("c-person").textContent = cop
    ? `${agoText(cop.last_person_at)} (${cop.source})`
    : "no person observed yet";
  $("c-person").className = cop ? "ok" : "";

  // sun (deterministic)
  $("c-sun").textContent = ctx.sun
    ? `${ctx.sun.is_daylight ? "up" : "down"} · az ${Math.round(ctx.sun.azimuth_deg)}° el ${Math.round(ctx.sun.elevation_deg)}°`
    : "—";

  // street context — structural OSM facts, unknowns stay unknowns
  const sc = ctx.street_context;
  $("c-streetctx").classList.toggle("dim", !sc);
  $("c-streetctx").innerHTML = sc ? [
    `sidewalk: ${sc.sidewalk ?? "untagged"}`,
    `lit: ${sc.lit ?? "untagged"}`,
    sc.camera_gap_m != null ? `camera spacing: ${sc.camera_gap_m} m` : null,
    sc.alley_dist_m != null ? `nearest alley: ${sc.alley_dist_m} m` : "nearest alley: unknown",
    sc.crossings_100m != null ? `crossings ≤100 m: ${sc.crossings_100m}` : "crossings: unknown",
  ].filter(Boolean).map((t) => `<div>${t}</div>`).join("")
    : "not built — run python -m ingest.statics";

  // a just-selected street's aggregate owns the refuge panel for one cycle
  if (streetRefugeHold) streetRefugeHold = false;
  else renderRefuge(`near ${ctx.location_desc || cid}`, ctx.refuge);
}
let streetRefugeHold = false;

function renderDetections(live) {
  const el = $("c-detections");
  if (!live || !live.ok) {
    el.textContent = live && live.why ? live.why : "none yet";
    el.classList.add("dim");
    return;
  }
  el.classList.remove("dim");
  const rows = live.detections.map((d) => {
    const pos = d.est ? ` @ ${d.est.range_m}m ${Math.round(d.est.bearing_deg)}°` : " (no position — bearing unresolved)";
    return `<div>${d.label} ${(d.conf * 100) | 0}%${pos}</div>`;
  });
  el.innerHTML = `<div class="dim">${live.analyzed_at} · ${live.model || ""}</div>` +
    (rows.join("") || "<div>nothing detected</div>") +
    (live.caption ? `<div class="dim" style="margin-top:4px">${live.caption}</div>` : "");
}

/* ------------------------------------------------------- FOV calibration */

document.querySelectorAll(".dir-btn").forEach((btn) => {
  btn.onclick = () => {
    if (!selected) return;
    const c = byId[selected];
    if (btn.dataset.abs !== undefined) {
      pendingBearing = parseFloat(btn.dataset.abs);
    } else {
      const base = pendingBearing !== null ? pendingBearing
        : (c.bearing_deg !== null && c.bearing_deg !== undefined ? c.bearing_deg
           : (c.road_axis_deg || 0));
      pendingBearing = (base + parseFloat(btn.dataset.rot) + 360) % 360;
    }
    renderBearingRow(c);
    refreshMapData();
  };
});

$("btn-confirm").onclick = async () => {
  if (!selected || pendingBearing === null) return;
  const b = await fetch(`${API}/api/bearing/${selected}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bearing_deg: pendingBearing }),
  }).then((r) => r.json());
  Object.assign(byId[selected], { bearing_deg: b.bearing_deg, bearing_conf: b.bearing_conf, bearing_basis: b.basis });
  pendingBearing = null;
  renderBearingRow(byId[selected]);
  refreshMapData();
  refreshCamDetail(selected);
};

$("btn-clear").onclick = async () => {
  if (!selected) return;
  await fetch(`${API}/api/bearing/${selected}`, { method: "DELETE" });
  Object.assign(byId[selected], { bearing_deg: null, bearing_conf: null, bearing_basis: null });
  pendingBearing = null;
  renderBearingRow(byId[selected]);
  refreshMapData();
  refreshCamDetail(selected);
};

$("btn-orient").onclick = async () => {
  if (!selected) return;
  $("c-basis").textContent = "running…";
  const b = await fetch(`${API}/api/orient/${selected}`, { method: "POST" }).then((r) => r.json());
  Object.assign(byId[selected], { bearing_deg: b.bearing_deg, bearing_conf: b.bearing_conf, bearing_basis: b.basis });
  pendingBearing = null;
  renderBearingRow(byId[selected]);
  refreshMapData();
  refreshCamDetail(selected);
};

$("btn-analyze").onclick = async () => {
  if (!selected) return;
  $("c-detections").textContent = "analyzing…";
  await fetch(`${API}/api/analyze/${selected}`, { method: "POST" });
  refreshCamDetail(selected);
  pollDetections();
};

/* ------------------------------------------------------------ detections */

/* ------------------------------------------------- pixel activity poll */

async function pollActivity() {
  try {
    const all = await fetch(`${API}/api/activity`).then((r) => r.json());
    for (const [cid, act] of Object.entries(all)) {
      if (!byId[cid]) continue;
      byId[cid].activity = act;
      byId[cid].active = act ? act.active : null;
    }
    refreshMapData();
    if (selected && byId[selected]) renderActivityRow(byId[selected]);
  } catch (_) { /* service restarting */ }
}
setInterval(pollActivity, 10000);

function renderActivityRow(c) {
  const act = c.activity;
  const el = $("c-activity");
  if (!act || act.active === null || act.active === undefined) {
    el.textContent = act && act.basis ? `unknown (${act.basis})` : "unknown";
    el.className = "";
    return;
  }
  el.textContent = act.active ? "ACTIVE (pixel change)" : "quiet";
  el.className = act.active ? "ok" : "warn";
}

async function pollDetections() {
  try {
    const all = await fetch(`${API}/api/detections`).then((r) => r.json());
    const feats = [];
    for (const [cid, live] of Object.entries(all)) {
      if (!live.ok || !live.detections) continue;
      if (byId[cid]) byId[cid].n_detections = live.detections.length;
      for (const d of live.detections) {
        if (!d.est) continue;
        feats.push({
          type: "Feature",
          properties: { label: d.label, cam: cid },
          geometry: { type: "Point", coordinates: [d.est.lon, d.est.lat] },
        });
      }
    }
    if (map.getSource("dets")) map.getSource("dets").setData({ type: "FeatureCollection", features: feats });
    refreshMapData();
  } catch (_) { /* service restarting */ }
}
setInterval(pollDetections, 5000);

/* ----------------------------------------------------------- sweep panel */

async function pollSweep() {
  try {
    const s = await fetch(`${API}/api/sweep/status`).then((r) => r.json());
    $("s-state").textContent = s.running ? "RUNNING" : "stopped";
    $("s-state").className = s.running ? "ok" : "warn";
    $("s-backend").textContent = s.vlm_available ? (s.backend || "configured") : "none configured";
    $("s-backend").className = s.vlm_available ? "ok" : "warn";
    $("s-pass").textContent = s.pass_no || 0;
    $("s-time").textContent = s.last_pass_s ? `${s.last_pass_s}s` : "—";
    $("s-analyzed").textContent = `${s.analyzed || 0} (${s.with_detections || 0} w/ objects)`;
    $("s-activity").textContent = `${s.activity_true || 0} active / ${s.activity_false || 0} quiet`;
  } catch (_) { /* service down */ }
}
$("btn-sweep-start").onclick = () => fetch(`${API}/api/sweep/start`, { method: "POST" }).then(pollSweep);
$("btn-sweep-stop").onclick = () => fetch(`${API}/api/sweep/stop`, { method: "POST" }).then(pollSweep);
setInterval(pollSweep, 3000);

/* ------------------------------------------------------------ map resize */

(() => {
  const handle = $("map-handle"), mapEl = $("map");
  let dragging = false;
  handle.addEventListener("pointerdown", (e) => { dragging = true; handle.setPointerCapture(e.pointerId); });
  handle.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const rect = $("center").getBoundingClientRect();
    const frac = Math.min(0.85, Math.max(0.2, (e.clientY - rect.top) / rect.height));
    mapEl.style.flex = `0 0 ${(frac * 100).toFixed(1)}%`;
    map.resize();
  });
  handle.addEventListener("pointerup", () => (dragging = false));
})();

/* -------------------------------------------------------------- boot */

loadStreets();
pollSweep();
pollDetections();
pollActivity();
