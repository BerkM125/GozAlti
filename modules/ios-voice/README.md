# modules/ios-voice — Apple speech + calling, native

What the browser could not reach: `SFSpeechRecognizer` and `AVSpeechSynthesizer`,
running on the phone.

## Why this exists next to `modules/audio-lm`

`audio-lm` owns the decision — a deterministic state machine with 26 tests covering its
safety properties. This app is **ears, voice, and a dial button**. The logic is
deliberately not duplicated in Swift: two implementations of safety-critical code drift,
and the phone's copy is the one nobody runs tests against.

The cost is a network dependency. No service, no dialogue — and the UI says so instead
of silently degrading.

## What it gets that the web version cannot

| | browser | this app |
|---|---|---|
| recognition | Chrome streams audio to **Google**; Safari uses Apple's engine but you cannot assert it | `requiresOnDeviceRecognition = true` — asserted, and shown in the UI |
| voices | whatever is installed, usually compact | full catalogue, prefers **Premium → Enhanced → Compact** |
| calling | not possible | `tel:` hands the number to the dialer |

## Build and run on your iPhone

```bash
brew install xcodegen          # already present on this Mac
cd modules/ios-voice
xcodegen generate
open GozAltiVoice.xcodeproj
```

In Xcode: select your iPhone, set **Signing & Capabilities → Team** to your Apple ID
(free provisioning gives a 7-day build — no paid account needed), then Run. First launch
asks for microphone and speech permissions; both are required.

Then in the app:
1. **laptop address** — `http://<your-mac-lan-ip>:8050`. Not `localhost`; that is the
   phone. Find it with `ipconfig getifaddr en0`.
2. **who to call** and **number** — a teammate. Start `audio-lm` on the Mac with
   `cd modules/audio-lm && ./run.sh start`.
3. Press **simulate off-path**. It speaks, then opens the mic by itself. Answer
   "no", then "yes".

## Calling

iOS gives no way to dial silently, and that is correct. `tel:` hands the number to the
Phone app and the user still taps to connect — so a human confirms twice, once by voice
and once in the dialer. For something that could summon a person at 2am that is a
property worth keeping, not a limitation to route around.

Per `modules/offpath-911`'s binding rules this only ever dials a nominated contact.
**Emergency services are never dialed from this codebase.**

The simulator has no dialer; calling only works on a real device.
