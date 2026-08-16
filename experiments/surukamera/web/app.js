/* SuruKamera frontend */
"use strict";

const $ = (s) => document.querySelector(s);
const state = {
  streets: [], cameras: [], street: null, pairs: [],
  pair: null, data: null, hlsPlayers: [], countdown: 60, timer: null,
  // user-adjustable layout: pane split ratios + per-camera pan/zoom.
  // survives the 60 s refresh re-render.
  stackRatio: 0.5, splitRatio: 0.5, mapRatio: 0.4,
  userAdjust: {},   // camera_id -> {dx, dy, zoom}
};

const REFRESH_S = 60;

/* ------------------------------------------------- map ----------------- */
const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: {
      carto: {
        type: "raster", tiles: ["/api/tile/{z}/{x}/{y}"], tileSize: 256,
        attribution: "© OpenStreetMap © CARTO | sat: Esri | cams: City of Seattle SDOT",
      },
      esri: { type: "raster", tiles: ["/api/sat-tile/{z}/{x}/{y}"], tileSize: 256 },
    },
    layers: [
      { id: "base", type: "raster", source: "carto" },
      { id: "base-sat", type: "raster", source: "esri", layout: { visibility: "none" } },
    ],
  },
  center: [-122.335, 47.61], zoom: 12, attributionControl: { compact: true },
});

let satOn = false;
function toggleSat() {
  satOn = !satOn;
  map.setLayoutProperty("base-sat", "visibility", satOn ? "visible" : "none");
  $("#btn-sat").classList.toggle("on", satOn);
}

map.on("load", () => {
  map.addSource("cams", { type: "geojson", data: emptyFC() });
  map.addSource("street-line", { type: "geojson", data: emptyFC() });
  map.addSource("cones", { type: "geojson", data: emptyFC() });

  map.addLayer({
    id: "street-line", type: "line", source: "street-line",
    paint: { "line-color": "#4FD1C5", "line-width": 2, "line-opacity": 0.5 },
  });
  map.addLayer({
    id: "cones", type: "fill", source: "cones",
    paint: { "fill-color": ["get", "color"], "fill-opacity": ["get", "opacity"] },
  });
  map.addLayer({
    id: "cams", type: "circle", source: "cams",
    paint: {
      "circle-radius": ["case", ["get", "active"], 6, ["get", "onStreet"], 4.5, 2.5],
      "circle-color": ["case", ["get", "active"], "#F6AD55", ["get", "onStreet"], "#4FD1C5", "#3A4750"],
      "circle-stroke-width": ["case", ["get", "active"], 1.5, 0],
      "circle-stroke-color": "#0B0E11",
    },
  });
  refreshMapCams();
});

const emptyFC = () => ({ type: "FeatureCollection", features: [] });

function refreshMapCams() {
  if (!map.getSource("cams")) return;
  const onStreet = new Set(state.pairs.flatMap((p) => [p.a, p.b]));
  const active = state.pair ? new Set([state.pair.a, state.pair.b]) : new Set();
  map.getSource("cams").setData({
    type: "FeatureCollection",
    features: state.cameras.map((c) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [c.lon, c.lat] },
      properties: {
        active: active.has(c.camera_id),
        onStreet: onStreet.has(c.camera_id),
      },
    })),
  });
}

function drawStreetLine() {
  if (!map.getSource("street-line")) return;
  const byId = Object.fromEntries(state.cameras.map((c) => [c.camera_id, c]));
  const feats = state.pairs.map((p) => ({
    type: "Feature",
    geometry: {
      type: "LineString",
      coordinates: [[byId[p.a].lon, byId[p.a].lat], [byId[p.b].lon, byId[p.b].lat]],
    },
    properties: {},
  })).filter((f) => f.geometry.coordinates.every((c) => c[0]));
  map.getSource("street-line").setData({ type: "FeatureCollection", features: feats });
  const coords = feats.flatMap((f) => f.geometry.coordinates);
  if (coords.length) {
    const b = coords.reduce(
      (acc, c) => [Math.min(acc[0], c[0]), Math.min(acc[1], c[1]), Math.max(acc[2], c[0]), Math.max(acc[3], c[1])],
      [180, 90, -180, -90]);
    map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 60, maxZoom: 15, duration: 600 });
  }
}

/* View cones: wedge from computed bearing, opacity scaled by confidence. */
function coneFeature(lon, lat, bearingDeg, conf, color, lenM = 140, halfAngle = 18) {
  const pts = [[lon, lat]];
  const mPerDegLat = 110574, mPerDegLon = 111320 * Math.cos((lat * Math.PI) / 180);
  for (let a = -halfAngle; a <= halfAngle; a += 6) {
    const rad = ((bearingDeg + a) * Math.PI) / 180;
    pts.push([lon + (Math.sin(rad) * lenM) / mPerDegLon, lat + (Math.cos(rad) * lenM) / mPerDegLat]);
  }
  pts.push([lon, lat]);
  return {
    type: "Feature",
    geometry: { type: "Polygon", coordinates: [pts] },
    properties: { color, opacity: Math.max(0.1, Math.min(0.55, conf * 0.55)) },
  };
}

function drawCones() {
  if (!map.getSource("cones")) return;
  const feats = [];
  if (state.data) {
    for (const pane of Object.values(state.data.panes)) {
      const g = pane.geometry;
      if (g.road_axis_bearing_deg == null) continue;
      feats.push(coneFeature(pane.lon, pane.lat, g.road_axis_bearing_deg, g.confidence, "#F6AD55"));
      if (!g.direction_resolved) {
        // unresolved 180-deg twin, fainter
        feats.push(coneFeature(pane.lon, pane.lat, (g.road_axis_bearing_deg + 180) % 360, g.confidence * 0.35, "#F6AD55"));
      }
    }
  }
  map.getSource("cones").setData({ type: "FeatureCollection", features: feats });
}

/* --------------------------------------------- left rail --------------- */
async function loadStreets() {
  state.streets = await (await fetch("/api/streets")).json();
  state.cameras = await (await fetch("/api/cameras")).json();
  renderStreets();
  refreshMapCams();
}

function renderStreets() {
  const q = ($("#street-search").value || "").toLowerCase();
  const el = $("#street-list");
  el.innerHTML = "";
  for (const s of state.streets) {
    if (q && !s.name.toLowerCase().includes(q)) continue;
    const div = document.createElement("div");
    div.className = "street-item" + (state.street === s.name ? " active" : "");
    div.innerHTML = `<span class="name">${s.name}</span>
      <span class="meta">${s.cameras}cam${s.best_score != null ? ` <span class="score">${s.best_score.toFixed(2)}</span>` : ""}</span>`;
    div.onclick = () => selectStreet(s.name);
    el.appendChild(div);
  }
}
$("#street-search").addEventListener("input", renderStreets);

/* --------------------------------------------- pair bar ---------------- */
async function selectStreet(name) {
  state.street = name;
  renderStreets();
  state.pairs = await (await fetch(`/api/street/${encodeURIComponent(name)}/pairs`)).json();
  renderPairBar();
  drawStreetLine();
  refreshMapCams();
  // auto-select: best cached stackable pair, else first
  const best = [...state.pairs].sort((x, y) => {
    const sx = x.cached_decision?.layout === "STACKED_CONTINUITY" ? x.cached_decision.score : -1;
    const sy = y.cached_decision?.layout === "STACKED_CONTINUITY" ? y.cached_decision.score : -1;
    return sy - sx;
  })[0];
  if (best) selectPair(best);
}

function renderPairBar() {
  const el = $("#pairbar");
  el.innerHTML = "";
  for (const p of state.pairs) {
    const chip = document.createElement("div");
    const isActive = state.pair && state.pair.a === p.a && state.pair.b === p.b;
    chip.className = "pair-chip" + (isActive ? " active" : "");
    const cd = p.cached_decision;
    const tag = cd
      ? (cd.layout === "STACKED_CONTINUITY" ? `<span class="tag stack">STACK ${cd.score.toFixed(2)}</span>` : `<span class="tag">SPLIT</span>`)
      : "";
    chip.innerHTML = `${p.a} ⇢ ${p.b} · ${Math.round(p.gap_m)}m${tag}`;
    chip.title = `${p.a_desc} → ${p.b_desc}`;
    chip.onclick = () => selectPair(p);
    el.appendChild(chip);
  }
}

/* --------------------------------------------- pair view --------------- */
async function selectPair(p) {
  state.pair = p;
  renderPairBar();
  await loadPairFrames();
  resetCountdown();
}

async function loadPairFrames() {
  if (!state.pair) return;
  const { a, b } = state.pair;
  const r = await fetch(`/api/pair/${a}/${b}/frames`);
  if (!r.ok) { console.error(await r.text()); return; }
  state.data = await r.json();
  renderViewport();
  renderDiagnostics();
  refreshMapCams();
  drawCones();
}

function destroyPlayers() {
  for (const h of state.hlsPlayers) { try { h.destroy(); } catch (e) {} }
  state.hlsPlayers = [];
}

function mediaEl(pane, stacked) {
  const wrap = document.createElement("div");
  wrap.className = "media-wrap";
  let el;
  if (pane.mode === "stream" && window.Hls && Hls.isSupported()) {
    el = document.createElement("video");
    el.muted = true; el.autoplay = true; el.playsInline = true;
    const hls = new Hls();
    hls.loadSource(pane.hls);
    hls.attachMedia(el);
    hls.on(Hls.Events.MANIFEST_PARSED, () => { el.play().catch(() => {}); });
    hls.on(Hls.Events.ERROR, (_e, d) => {
      if (d.fatal) { hls.destroy(); fallbackToSnapshot(wrap, pane, stacked); }
    });
    state.hlsPlayers.push(hls);
  } else {
    el = snapshotImg(pane);
  }
  applyMediaTransform(el, pane, stacked);
  wrap.appendChild(el);
  attachMediaAdjust(wrap, pane, stacked);
  return wrap;
}

function snapshotImg(pane) {
  const img = document.createElement("img");
  img.className = "snap";
  img.src = `${pane.snapshot}?t=${Date.now()}`;
  return img;
}

function fallbackToSnapshot(wrap, pane, stacked) {
  wrap.innerHTML = "";
  const img = snapshotImg(pane);
  applyMediaTransform(img, pane, stacked);
  wrap.appendChild(img);
}

/* Continuity base transform: rotate so the line from bottom-centre to the
   vanishing point turns vertical (the VP lands on the pane's centre axis
   and the two road segments read as one corridor), plus a cover-scale that
   hides the rotated corners. */
function continuityBase(pane) {
  const vp = pane.geometry.vanishing_point;
  if (!vp) return { deg: 0, scale: 1 };
  const alpha = Math.atan2(vp[0] - 0.5, Math.max(0.15, 1.0 - vp[1]));
  const deg = Math.max(-14, Math.min(14, (alpha * 180) / Math.PI));
  const rad = Math.abs((deg * Math.PI) / 180);
  const scale = Math.cos(rad) + 2.4 * Math.sin(rad) + 0.04;
  return { deg, scale };
}

function userAdjustFor(cid) {
  return (state.userAdjust[cid] ||= { dx: 0, dy: 0, zoom: 1 });
}

/* Full media transform = computed continuity base × user pan/zoom. */
function applyMediaTransform(el, pane, stacked) {
  const base = stacked ? continuityBase(pane) : { deg: 0, scale: 1 };
  const u = userAdjustFor(pane.camera_id);
  el.style.transform =
    `translate(-50%,-50%) translate(${u.dx.toFixed(1)}px,${u.dy.toFixed(1)}px) ` +
    `rotate(${(-base.deg).toFixed(2)}deg) scale(${(base.scale * u.zoom).toFixed(3)})`;
}

/* Direct manipulation of a feed: drag to pan, wheel to zoom,
   double-click to reset. Adjustments persist across refreshes. */
function attachMediaAdjust(wrap, pane, stacked) {
  const media = () => wrap.querySelector("video, img.snap");
  const apply = () => { const m = media(); if (m) applyMediaTransform(m, pane, stacked); };

  wrap.addEventListener("wheel", (e) => {
    e.preventDefault();
    const u = userAdjustFor(pane.camera_id);
    u.zoom = Math.min(4, Math.max(0.4, u.zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1)));
    apply();
  }, { passive: false });

  wrap.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    wrap.setPointerCapture(e.pointerId);
    wrap.classList.add("dragging");
    let lastX = e.clientX, lastY = e.clientY;
    const move = (ev) => {
      const u = userAdjustFor(pane.camera_id);
      u.dx += ev.clientX - lastX;
      u.dy += ev.clientY - lastY;
      lastX = ev.clientX; lastY = ev.clientY;
      apply();
    };
    const up = () => {
      wrap.classList.remove("dragging");
      wrap.removeEventListener("pointermove", move);
      wrap.removeEventListener("pointerup", up);
      wrap.removeEventListener("pointercancel", up);
    };
    wrap.addEventListener("pointermove", move);
    wrap.addEventListener("pointerup", up);
    wrap.addEventListener("pointercancel", up);
  });

  wrap.addEventListener("dblclick", () => {
    state.userAdjust[pane.camera_id] = { dx: 0, dy: 0, zoom: 1 };
    apply();
  });
}

function paneLabel(pane, extra) {
  const g = pane.geometry;
  const live = pane.mode === "stream";
  const age = Math.max(0, Math.round((state.data.server_time - pane.fetched_at)));
  const ageBadge = live
    ? `<span class="badge live">LIVE</span>`
    : (age > 300 ? `<span class="badge old">SNAP ${fmtAge(age)}</span>` : `<span class="badge snap">SNAP ${fmtAge(age)}</span>`);
  const bearing = g.road_axis_bearing_deg != null
    ? `${Math.round(g.road_axis_bearing_deg)}°${g.direction_resolved ? "" : "±180"}` : "—";
  return `<div class="pane-label">${ageBadge}<span class="desc">${pane.desc}</span>
    <span style="color:var(--dim)">${bearing}</span>${extra || ""}</div>
    <div class="pane-ts">${pane.camera_id} · fetched ${new Date(pane.fetched_at * 1000).toLocaleTimeString()}</div>`;
}

const fmtAge = (s) => (s < 90 ? `${s}s` : `${Math.round(s / 60)}m`);

function renderViewport() {
  destroyPlayers();
  const vp = $("#viewport");
  vp.innerHTML = "";
  const d = state.data;
  const stacked = d.decision.layout === "STACKED_CONTINUITY";

  const container = document.createElement("div");
  container.className = stacked ? "stacked" : "split";

  if (stacked) {
    const top = paneDiv(d.panes.top, true, `<span style="color:var(--dim)">↑ downstream</span>`);
    const seam = document.createElement("div");
    seam.className = "seam";
    seam.innerHTML = `<div class="feather-top"></div><div class="rule"></div>
      <div class="feather-bot"></div><div class="gap-label">≈ ${Math.round(d.gap_m)} m gap</div>
      <div class="grab" title="drag to resize panes"></div>`;
    const bottom = paneDiv(d.panes.bottom, true, `<span style="color:var(--dim)">↓ upstream</span>`);
    top.style.flex = `${state.stackRatio} 1 0px`;
    bottom.style.flex = `${1 - state.stackRatio} 1 0px`;
    container.append(top, seam, bottom);
    attachDividerDrag(seam.querySelector(".grab"), container, "y", (r) => {
      state.stackRatio = r;
      top.style.flex = `${r} 1 0px`;
      bottom.style.flex = `${1 - r} 1 0px`;
    });
  } else {
    const left = paneDiv(d.panes.left, false);
    const divider = document.createElement("div");
    divider.className = "divider";
    divider.title = "drag to resize panes";
    const right = paneDiv(d.panes.right, false);
    left.style.flex = `${state.splitRatio} 1 0px`;
    right.style.flex = `${1 - state.splitRatio} 1 0px`;
    container.append(left, divider, right);
    attachDividerDrag(divider, container, "x", (r) => {
      state.splitRatio = r;
      left.style.flex = `${r} 1 0px`;
      right.style.flex = `${1 - r} 1 0px`;
    });
  }
  vp.appendChild(container);
}

/* Generic divider drag: reports a clamped ratio along the given axis. */
function attachDividerDrag(handle, container, axis, onRatio) {
  handle.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    e.stopPropagation();
    handle.setPointerCapture(e.pointerId);
    document.body.classList.add("resizing");
    const move = (ev) => {
      const rect = container.getBoundingClientRect();
      const r = axis === "y"
        ? (ev.clientY - rect.top) / rect.height
        : (ev.clientX - rect.left) / rect.width;
      onRatio(Math.min(0.85, Math.max(0.15, r)));
    };
    const up = () => {
      document.body.classList.remove("resizing");
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.removeEventListener("pointercancel", up);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
    handle.addEventListener("pointercancel", up);
  });
}

function paneDiv(pane, stacked, extra) {
  const div = document.createElement("div");
  div.className = "pane";
  div.appendChild(mediaEl(pane, stacked));
  div.insertAdjacentHTML("beforeend", paneLabel(pane, extra));
  return div;
}

/* --------------------------------------------- diagnostics ------------- */
function renderDiagnostics() {
  const d = state.data.decision;
  $("#diag-empty").style.display = "none";
  $("#diag").style.display = "block";
  const rel = $("#d-rel");
  rel.textContent = d.relationship;
  rel.className = d.relationship === "CO_DIRECTIONAL" ? "ok" : (d.relationship === "UNKNOWN" ? "warn" : "");
  const lay = $("#d-layout");
  lay.textContent = d.layout;
  lay.className = d.layout === "STACKED_CONTINUITY" ? "ok" : "";
  $("#d-hd").textContent = d.heading_delta != null ? `${d.heading_delta}°` : "—";
  $("#d-gap").textContent = state.data.gap_m ? `${Math.round(state.data.gap_m)} m` : "—";
  $("#d-score").textContent = d.score != null ? d.score.toFixed(3) : "—";

  const comps = $("#d-components");
  comps.innerHTML = "";
  const weights = { heading_agreement: 0.35, vp_x_alignment: 0.25, horizon_agreement: 0.15, slope_sign_agreement: 0.15, distance_plausibility: 0.10 };
  for (const [k, v] of Object.entries(d.components || {})) {
    comps.insertAdjacentHTML("beforeend",
      `<div class="comp-row"><span>${k.replaceAll("_", " ")} ·${weights[k] ?? ""}</span>
       <div class="comp-bar"><i style="width:${Math.round(v * 100)}%"></i></div>
       <span class="val">${Number(v).toFixed(2)}</span></div>`);
  }

  const reason = $("#d-reason");
  reason.textContent = d.reason;
  reason.className = "reason" + (d.relationship === "UNKNOWN" ? " warn" : "");
  $("#d-basis").textContent = d.direction_basis + (d.notes?.length ? " — " + d.notes.join("; ") : "");

  const pd = $("#d-panes");
  pd.innerHTML = "";
  const DIRS = [["N", 0], ["NE", 45], ["E", 90], ["SE", 135], ["S", 180], ["SW", 225], ["W", 270], ["NW", 315]];
  for (const [pos, pane] of Object.entries(state.data.panes)) {
    const g = pane.geometry;
    const div = document.createElement("div");
    div.className = "pane-diag";
    const layerLines = (g.bearing_layers || [])
      .map((l) => `<div class="${l.ok ? "" : "dimline"}">▸ ${l.layer}${l.ok ? " ✓" : " ✗"}${l.bearing_deg != null ? ` ${Math.round(l.bearing_deg)}°` : ""}${l.why ? ` — ${l.why}` : ""}</div>`)
      .join("");
    const cur = g.road_axis_bearing_deg;
    div.innerHTML = `
      <div><span class="id">${pane.camera_id}</span> · ${pos} · ${pane.mode}</div>
      <div>vp ${g.vanishing_point ? g.vanishing_point.map((x) => x.toFixed(2)).join(",") : "—"}
        · conf ${g.confidence} · slope ${g.slope_sign > 0 ? "+1" : g.slope_sign < 0 ? "−1" : "0"}</div>
      <div>method ${g.method}</div>
      <div>flow ${g.flow ? `t${g.flow.toward ?? "—"}/a${g.flow.away ?? "—"} n=${g.flow.n}${g.flow.pan ? " ⚠PAN" : ""}` : "—"}</div>
      <div class="layer-list">${layerLines}</div>
      <img class="sat-thumb" src="/api/satellite/${pane.camera_id}" alt="satellite"
           title="A = way direction, B = opposite" loading="lazy">
      <div class="dir-grid" data-cam="${pane.camera_id}"></div>`;
    const grid = div.querySelector(".dir-grid");
    for (const [label, deg] of DIRS) {
      const b = document.createElement("button");
      b.className = "dir-btn";
      b.textContent = label;
      if (cur != null && g.direction_resolved
          && Math.min(Math.abs(cur - deg), 360 - Math.abs(cur - deg)) <= 22.5) {
        b.classList.add("est");
      }
      b.title = `confirm: camera faces ${label} (${deg}°)`;
      b.onclick = async () => {
        await fetch(`/api/bearing/${pane.camera_id}`,
          { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ bearing_deg: deg }) });
        await loadPairFrames();
      };
      grid.appendChild(b);
    }
    const auto = document.createElement("button");
    auto.className = "dir-btn auto";
    auto.textContent = "auto";
    auto.title = "clear manual override, use automated layers";
    auto.onclick = async () => {
      await fetch(`/api/bearing/${pane.camera_id}`, { method: "DELETE" });
      await loadPairFrames();
    };
    grid.appendChild(auto);
    pd.appendChild(div);
  }

  const stale = d.direction_basis === "stale" || (d.reason || "").includes("STALE");
  const anyPan = Object.values(state.data.panes).some((p) => p.geometry.flow?.pan);
  const ptz = $("#d-ptz");
  if (stale || anyPan) {
    ptz.textContent = anyPan ? "⚠ pan in progress — camera being re-aimed; continuity suspended" : d.reason;
    ptz.className = "reason warn";
  } else {
    ptz.textContent = "no drift detected between recent samples";
    ptz.className = "reason";
  }
}

/* --------------------------------------------- refresh loop ------------ */
function resetCountdown() {
  state.countdown = REFRESH_S;
  if (state.timer) clearInterval(state.timer);
  state.timer = setInterval(async () => {
    state.countdown -= 1;
    $("#countdown").textContent = `${state.countdown}s`;
    if (state.countdown <= 0) {
      state.countdown = REFRESH_S;
      await loadPairFrames();
    }
  }, 1000);
}
$("#btn-refresh").onclick = () => { loadPairFrames(); state.countdown = REFRESH_S; };

/* --------------------------------------------- boot -------------------- */
attachDividerDrag($("#map-handle"), $("#center"), "y", (r) => {
  state.mapRatio = r;
  $("#map").style.flex = `0 0 ${(r * 100).toFixed(1)}%`;
  map.resize();
});
loadStreets();
