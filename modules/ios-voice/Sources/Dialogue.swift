import Foundation
import UIKit

/// Client for the audio-lm state machine on :8050.
///
/// The decision logic deliberately does NOT live in this app. It is safety-critical —
/// it decides whether an escalation is reported — and duplicating it in Swift would mean
/// two implementations that can drift, with the phone's copy being the one nobody tests.
/// modules/audio-lm/dialogue.py is the single source of truth and has 26 tests against
/// its safety properties. This app is ears, voice, and a dial button.
///
/// The cost is a network dependency: no service, no dialogue. That is the honest trade
/// and the UI says so rather than silently degrading.
@MainActor
final class DialogueClient: ObservableObject {
    @Published var state = "idle"
    @Published var say = ""
    @Published var done = false
    @Published var escalated = false
    @Published var contact = "Dhruv"
    @Published var contactNumber = ""
    @Published var lastError: String?
    @Published var turns: [[String: Any]] = []

    /// Your laptop on the LAN. Override in the UI — the phone cannot reach "localhost".
    @Published var baseURL = "http://172.16.95.111:8050"
    private var sessionID: String?

    var awaitingAnswer: Bool { state == "asked_wellbeing" || state == "asked_escalate" }

    func start(trigger: String) async {
        sessionID = nil; escalated = false; done = false; turns = []
        await post("/session", ["trigger": trigger, "contact": contact])
    }

    func hear(_ transcript: String, confidence: Double) async {
        guard let sid = sessionID else { return }
        await post("/session/\(sid)/hear",
                   ["transcript": transcript, "confidence": confidence,
                    "evidence": ["source": "ios-voice"]])
    }

    func cancel() async {
        guard let sid = sessionID else { return }
        await post("/session/\(sid)/cancel", [:])
    }

    private func post(_ path: String, _ body: [String: Any]) async {
        guard let url = URL(string: baseURL + path) else {
            lastError = "Bad server URL"; return
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 10
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        do {
            let (data, _) = try await URLSession.shared.data(for: req)
            guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            else { lastError = "Bad response"; return }
            sessionID = obj["session_id"] as? String ?? sessionID
            state = obj["state"] as? String ?? state
            say = obj["say"] as? String ?? ""
            done = obj["done"] as? Bool ?? false
            escalated = obj["escalated"] as? Bool ?? false
            lastError = nil
            if let sid = sessionID { await refresh(sid) }
        } catch {
            lastError = "Cannot reach \(baseURL) — \(error.localizedDescription)"
        }
    }

    private func refresh(_ sid: String) async {
        guard let url = URL(string: "\(baseURL)/session/\(sid)") else { return }
        if let (data, _) = try? await URLSession.shared.data(from: url),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let t = obj["transcript"] as? [[String: Any]] {
            turns = t
        }
    }

    /// Place the call.
    ///
    /// iOS gives no way to dial silently, and that is correct: `tel:` hands the number to
    /// the Phone app and the user still taps to connect. So the human confirms twice —
    /// once by voice to this app, once in the dialer. For a feature that could summon
    /// someone at 2am that is a property worth having, not a limitation to route around.
    ///
    /// Per modules/offpath-911's binding rules this only ever dials a nominated contact.
    /// Emergency services are never dialed from this codebase.
    func placeCall() -> String {
        let digits = contactNumber.filter { $0.isNumber || $0 == "+" }
        guard !digits.isEmpty else { return "No number set for \(contact)." }
        guard let url = URL(string: "tel://\(digits)"),
              UIApplication.shared.canOpenURL(url) else {
            return "This device cannot place calls (simulator has no dialer)."
        }
        UIApplication.shared.open(url)
        return "Opening the dialer for \(contact) — you still tap to connect."
    }
}
