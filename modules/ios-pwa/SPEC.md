# modules/ios-pwa — iOS PWA constraints (the module formerly known as "iOS port")

**Owner:** Adi · **Effort budget:** 1 h

There is no native port to build: `modules/walk-app` is already a PWA
(`manifest.webmanifest`, `display: standalone`). iOS-vs-Android is the wrong
question; this module documents what iOS Safari actually permits and owns the
one test that can kill demo day.

## The test that matters (binary, ~20 min, on the ACTUAL demo phone)

> Does `navigator.geolocation` work on the demo phone, over the demo network,
> in the demo browser?

iOS Safari **blocks geolocation in non-secure contexts**: `http://<lan-ip>` and
`http://<tailnet-ip>` fail SILENTLY (no prompt, no error surface a user sees).
`localhost` is exempt; HTTPS works. Run the test before any location feature is
declared done, and record the result here:

- [ ] geolocation over the demo URL on the demo phone: PASS / FAIL
- [ ] add-to-home-screen behaves (standalone, no Safari chrome): PASS / FAIL

## Mitigations if the test fails

1. `tailscale cert` — real cert for the tailnet hostname (fastest if we demo
   over tailnet anyway).
2. `mkcert` — local CA, install its root on the demo phone once.
3. Fall back to demoing the live-location piece from a laptop browser
   (secure-context rules differ) and keep the phone for the rest.

## iOS PWA limits to design around (documented, not fought)

- **No background geolocation.** Position updates stop the moment the app
  backgrounds or the screen locks → `modules/offpath-911` must be demoed with
  the screen on; "app running while phone turned off" from the original story
  is NOT achievable in a PWA and nobody should burn hours trying.
- Push notifications need iOS 16.4+ AND home-screen install — treat as
  unavailable for the demo; in-app banners instead.
- No Android-style install prompt; add-to-home-screen is manual via the share
  sheet.
- Audio: `getUserMedia` (mic, for `modules/audio-lm`) ALSO requires a secure
  context — same fix as geolocation, verify in the same 20 minutes.

## Definition of done

Both checkboxes above filled in from the real device, the chosen mitigation
written down, and walk-app's README pointing here for the demo-network setup.
