# Discovered endpoints (M0)

All verified 2026-08-15. None require Playwright at runtime — every endpoint
below works with a bare HTTP client. (Note: this machine's network blocks
UDP/53; the app routes all traffic through an in-process DNS-over-TCP
CONNECT proxy, see `app/netboot.py`.)

## 1. Camera inventory (canonical) — ArcGIS Feature Server

```
GET https://services.arcgis.com/ZOyb2t4B0UYuYNYH/arcgis/rest/services/Traffic_Cameras_CDL/FeatureServer/0/query
    ?where=1%3D1&outFields=*&outSR=4326&f=geojson
```

658 point features. Fields: `OBJECTID, COMPKEY, UNITID, OWNERSHIP, DISTRICT,
NAME, COMPTYPE, URL, LOCATION, SERVSTAT, SE_ANNO_CAD_DATA, STREAM_NAME,
GLOBALID, CREATOR, CREATE_DATE, EDITOR, EDIT_DATE`.

- `UNITID` — stable camera id (`CMR-0270`), join key to traveler map.
- `NAME` — image filename (`MLK_S_Jackson_NS.jpg`); `CAMERA_KEY` = `NAME` minus `.jpg`.
- `URL` — full snapshot URL (http; use https).
- `OWNERSHIP` — `SDOT` (387) or `WSDOT` (263).
- `STREAM_NAME` — present on 382 rows; matches the key but `NAME`-derived key is more complete.
- No heading/bearing field exists. Bearing must be image-derived.

## 2. Snapshot images

- SDOT: `https://www.seattle.gov/trafficcams/images/{KEY}.jpg?{cachebuster}`
- WSDOT: `https://images.wsdot.wa.gov/nw/{KEY}.jpg?{cachebuster}`

Cachebuster = epoch millis. Refresh cadence ~1-2 min. No auth, no Referer.

## 3. Traveler map MVC API (`https://web.seattle.gov/Travelers/api/…`)

| Endpoint | Notes |
|---|---|
| `Map/GetAllNeighborhoods` | 13 neighborhoods (double-encoded JSON string) |
| `Map/GetCamerasByNeighborhood?neighborhood=Downtown` | camera list: `Id` (== UNITID), `Description`, `ImageUrl`, `Type` (sdot/wsdot) |
| `Map/GetCamerasByAddress?latitude=&longitude=` | nearby cameras |
| `Map/Data?zoomId=13&type=2` | clustered camera map points; `type=1` = incidents |
| `Map/WowsaUrl` | returns the HLS URL template (below) |
| `Map/MapKey` | Bing Maps key for their map (unused by us) |

Responses are sometimes JSON-encoded-as-a-JSON-string; decode twice.

## 4. Live video — HLS (Wowza) ★

```
https://61e0c5d388c2e.streamlock.net:443/live/{KEY}.stream/playlist.m3u8
```

- `{KEY}` = snapshot filename minus `.jpg` (SDOT cameras only; WSDOT have no streams).
- **Case 1 of the spec: works completely bare.** No token, no expiry, no
  Referer, no cookie. `Access-Control-Allow-Origin: *` — browsers can play it
  directly with hls.js. Verified via curl with no headers.
- Master playlist → `chunklist_w*.m3u8` → MPEG-TS segments (~2 s each,
  720x480 H.264). Segment/chunklist URLs are relative — trivial to proxy.
- Probe result: **357 of 387 SDOT cameras have a live, working stream.**
- The blob: URL seen in the page (`blob:https://web.seattle.gov/…`) is just
  hls.js feeding MSE; the real source is the URL above
  (see `data/camera.video.js` line 40: `getWowsaUrl().replace("{stream}",
  camera.ImageUrl.replace('.jpg', '.stream'))`).

## 5. Terms / robots

- `www.seattle.gov/robots.txt` does not disallow `/trafficcams/`.
- City of Seattle Open Data terms: public data, attribution requested
  ("City of Seattle, Seattle Department of Transportation" — copyright text
  carried in the ArcGIS layer). Cameras are explicitly published for public
  traveler information. This client sends a descriptive User-Agent and rate
  limits: ≥60 s between snapshot fetches per camera, ≤4 concurrent requests.
  HLS segments are pulled only while a stream is actively displayed/analyzed.
