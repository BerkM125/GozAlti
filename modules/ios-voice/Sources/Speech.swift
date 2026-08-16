import AVFoundation
import Speech
import SwiftUI

/// Apple's speech stack: SFSpeechRecognizer for ears, AVSpeechSynthesizer for voice.
///
/// This is what the browser could not reach. Two things it buys us over the Web Speech
/// API, and both matter for a feature about someone in trouble:
///
///  1. `requiresOnDeviceRecognition` genuinely keeps audio on the phone. Chrome's
///     webkitSpeechRecognition streams microphone audio to Google; Safari uses Apple's
///     engine but does not let us *assert* on-device. Here we set the flag and report it.
///  2. The full installed voice catalogue, including Premium and Enhanced builds. The
///     compact builds are what sound chopped and robotic.
@MainActor
final class Speech: NSObject, ObservableObject {
    @Published var heard: String = ""
    @Published var confidence: Double = 0
    @Published var listening = false
    @Published var speaking = false
    @Published var onDevice = false
    @Published var voiceName = "—"
    @Published var permissionProblem: String?

    private let synth = AVSpeechSynthesizer()
    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private let engine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var voice: AVSpeechSynthesisVoice?
    private var onFinalTranscript: ((String, Double) -> Void)?
    private var silenceTimer: Timer?
    private var lastPartialAt = Date()
    /// SFSpeechRecognizer will happily keep a session open long after the person has
    /// stopped talking — it waits for its own end-of-utterance heuristic, which for a
    /// single word like "no" can take many seconds or never fire at all. That is why
    /// answering did nothing. We finalise ourselves once the transcript has been quiet.
    private let silenceCutoff: TimeInterval = 1.2
    private let hardCutoff: TimeInterval = 8.0
    private var listenStartedAt = Date()

    override init() {
        super.init()
        synth.delegate = self
        voice = Self.bestVoice()
        voiceName = voice.map { "\($0.name) [\(Self.qualityLabel($0))]" } ?? "system default"
        onDevice = recognizer?.supportsOnDeviceRecognition ?? false
    }

    // MARK: voice selection

    /// Premium > Enhanced > Compact. Only compact ships by default, which is exactly why
    /// the stock voice sounds clipped. Anything better the user has installed wins.
    static func bestVoice() -> AVSpeechSynthesisVoice? {
        let english = AVSpeechSynthesisVoice.speechVoices()
            .filter { $0.language.hasPrefix("en") }
        func firstWith(_ q: AVSpeechSynthesisVoiceQuality) -> AVSpeechSynthesisVoice? {
            english.first { $0.quality == q && $0.language == "en-US" }
                ?? english.first { $0.quality == q }
        }
        if #available(iOS 16.0, *), let premium = firstWith(.premium) { return premium }
        if let enhanced = firstWith(.enhanced) { return enhanced }
        return english.first { $0.name.contains("Samantha") } ?? english.first
    }

    static func qualityLabel(_ v: AVSpeechSynthesisVoice) -> String {
        switch v.quality {
        case .enhanced: return "Enhanced"
        case .premium: return "Premium"
        default: return "Compact — install a better voice in Settings › Accessibility"
        }
    }

    // MARK: permissions

    func requestPermissions() async {
        let speechOK: Bool = await withCheckedContinuation { cont in
            SFSpeechRecognizer.requestAuthorization { status in
                cont.resume(returning: status == .authorized)
            }
        }
        let micOK: Bool = await withCheckedContinuation { cont in
            AVAudioApplication.requestRecordPermission { cont.resume(returning: $0) }
        }
        permissionProblem = speechOK ? (micOK ? nil : "Microphone permission denied.")
                                     : "Speech recognition permission denied."
    }

    // MARK: speaking

    /// Speak, then hand control back. The completion is where listening starts — opening
    /// the mic while we are still talking makes the recogniser transcribe our own voice.
    func say(_ text: String, then: (() -> Void)? = nil) {
        guard !text.isEmpty else { then?(); return }
        stopListening()
        afterSpeaking = then
        let u = AVSpeechUtterance(string: text)
        u.voice = voice
        u.rate = AVSpeechUtteranceDefaultSpeechRate * 0.96
        u.pitchMultiplier = 1.0
        u.postUtteranceDelay = 0.15
        speaking = true
        synth.speak(u)
    }
    private var afterSpeaking: (() -> Void)?

    // MARK: listening

    func listen(onFinal: @escaping (String, Double) -> Void) {
        guard !listening else { return }
        onFinalTranscript = onFinal
        heard = ""; confidence = 0
        lastPartialAt = Date(); listenStartedAt = Date()

        do {
            let session = AVAudioSession.sharedInstance()
            // allowBluetoothHFP so the demo works through earbuds, which is the whole
            // point of a voice prompt while walking.
            let opts: AVAudioSession.CategoryOptions =
                [.duckOthers, .defaultToSpeaker, .allowBluetoothHFP]
            try session.setCategory(.playAndRecord, mode: .spokenAudio, options: opts)
            try session.setActive(true, options: .notifyOthersOnDeactivation)

            let req = SFSpeechAudioBufferRecognitionRequest()
            req.shouldReportPartialResults = true
            // The whole point of doing this natively: keep the audio on the phone.
            if recognizer?.supportsOnDeviceRecognition == true {
                req.requiresOnDeviceRecognition = true
            }
            request = req

            let input = engine.inputNode
            let format = input.outputFormat(forBus: 0)
            input.removeTap(onBus: 0)
            input.installTap(onBus: 0, bufferSize: 1024, format: format) { buf, _ in
                req.append(buf)
            }
            engine.prepare()
            try engine.start()
            listening = true
            startSilenceWatch()

            task = recognizer?.recognitionTask(with: req) { [weak self] result, error in
                guard let self else { return }
                Task { @MainActor in
                    if let result {
                        let best = result.bestTranscription
                        self.heard = best.formattedString
                        // SFSpeechRecognizer gives per-segment confidence; the mean is a
                        // fair summary and it is 0 until the result is final.
                        let segs = best.segments
                        let mean = segs.isEmpty ? 0
                            : Double(segs.map(\.confidence).reduce(0, +)) / Double(segs.count)
                        self.confidence = mean
                        if !best.formattedString.isEmpty { self.lastPartialAt = Date() }
                        if result.isFinal { self.finalizeNow() }
                    }
                    if error != nil { self.stopListening() }
                }
            }
        } catch {
            permissionProblem = "Audio session failed: \(error.localizedDescription)"
            stopListening()
        }
    }

    /// Finalise on our own terms: a short quiet gap after speech, or a hard ceiling so a
    /// noisy street cannot hold the mic open forever.
    private func startSilenceWatch() {
        silenceTimer?.invalidate()
        silenceTimer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.listening else { return }
                let quiet = Date().timeIntervalSince(self.lastPartialAt)
                let total = Date().timeIntervalSince(self.listenStartedAt)
                let spoke = !self.heard.trimmingCharacters(in: .whitespaces).isEmpty
                if (spoke && quiet >= self.silenceCutoff) || total >= self.hardCutoff {
                    self.finalizeNow()
                }
            }
        }
    }

    private func finalizeNow() {
        let text = heard.trimmingCharacters(in: .whitespaces)
        // Partial results carry no per-segment confidence yet. Treat a clearly-heard
        // partial as moderately confident and let the state machine's own threshold
        // decide — it refuses anything under 0.55 and re-asks.
        let conf = confidence > 0 ? confidence : (text.isEmpty ? 0.0 : 0.7)
        stopListening()
        guard !text.isEmpty else { return }
        onFinalTranscript?(text, conf)
        onFinalTranscript = nil
    }

    func stopListening() {
        silenceTimer?.invalidate(); silenceTimer = nil
        guard listening || engine.isRunning else { return }
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        request?.endAudio()
        task?.cancel()
        request = nil; task = nil
        listening = false
    }
}

extension Speech: AVSpeechSynthesizerDelegate {
    nonisolated func speechSynthesizer(_ s: AVSpeechSynthesizer,
                                       didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in
            self.speaking = false
            let cb = self.afterSpeaking
            self.afterSpeaking = nil
            cb?()
        }
    }
    nonisolated func speechSynthesizer(_ s: AVSpeechSynthesizer,
                                       didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor in self.speaking = false; self.afterSpeaking = nil }
    }
}
