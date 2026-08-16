import Foundation

/// The confirmation state machine, on the phone.
///
/// This is a faithful port of `modules/audio-lm/dialogue.py`, which has 26 tests over its
/// safety properties. Duplicating safety-critical logic is a real cost — two copies can
/// drift — but the alternative was worse: requiring a laptop on the same network for the
/// phone to ask whether you are okay. This feature matters precisely when you are away
/// from wifi, so a network dependency in the decision path is the wrong trade.
///
/// Keep this file and dialogue.py in step. The Python side is the reference; if they
/// disagree, the Python tests decide.
///
/// SAFETY PROPERTIES (mirroring test_dialogue.py):
///  1. ESCALATE needs TWO explicit affirmatives — never one, never silence.
///  2. Unrecognised input re-asks; after 2 re-prompts it lands on needsAttention.
///  3. Low-confidence transcripts are UNKNOWN, not a best guess.
///  4. A transcript with both yes and no refuses to guess.
///  5. Cancel wins in every state, including after ESCALATE.
enum DState: String {
    case idle, askedWellbeing = "asked_wellbeing", askedEscalate = "asked_escalate"
    case resolvedOK = "resolved_ok", escalate, needsAttention = "needs_attention", cancelled
    var terminal: Bool {
        [.resolvedOK, .escalate, .needsAttention, .cancelled].contains(self)
    }
    var awaitingAnswer: Bool { self == .askedWellbeing || self == .askedEscalate }
}

enum DIntent: String { case yes, no, cancel, help, unknown }

struct DTurn: Identifiable {
    let id = UUID()
    let from: DState, to: DState
    let heard: String, confidence: Double, intent: DIntent, said: String
}

@MainActor
final class LocalDialogue: ObservableObject {
    @Published private(set) var state: DState = .idle
    @Published private(set) var say = ""
    @Published private(set) var turns: [DTurn] = []
    @Published var contact = "Dhruv"

    private var reprompts = 0
    static let minConfidence = 0.55
    static let maxReprompts = 2

    var escalated: Bool { state == .escalate }
    var done: Bool { state.terminal }

    // MARK: intent

    /// Whole-word matching only, so "now" is not "no" and "yesterday" is not "yes".
    /// Ambiguity is resolved by refusing, never by guessing.
    static func classify(_ transcript: String, _ confidence: Double) -> DIntent {
        guard confidence >= minConfidence else { return .unknown }
        let t = transcript.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return .unknown }

        func hit(_ pattern: String) -> Bool {
            t.range(of: "\\b(\(pattern))\\b", options: [.regularExpression]) != nil
        }
        let cancel = hit("cancel|stop|never ?mind|nevermind|abort|forget it|false alarm|disregard")
        let help = hit("help|emergency|call 9 ?1 ?1|nine one one|i'?m in danger|somebody help")
        let no = hit("no|nope|nah|negative|not (ok|okay|good|fine|really)|i'?m not (ok|okay|good|fine)")
        let yes = hit("yes|yeah|yep|yup|correct|affirmative|do it|please do|go ahead|i'?m (ok|okay|good|fine)|all good")

        if cancel { return .cancel }
        if help { return .help }
        if yes && no { return .unknown }   // both present: refuse to guess, re-ask
        if yes { return .yes }
        if no { return .no }
        return .unknown
    }

    // MARK: prompts — we call a PERSON, never emergency services

    private var who: String { contact.trimmingCharacters(in: .whitespaces).isEmpty
        ? "your emergency contact" : contact }

    private func prompt(_ s: DState) -> String {
        switch s {
        case .askedWellbeing: return "Hey, I noticed you went off path. Everything good?"
        case .askedEscalate:  return "Do you want me to call \(who) and share your location?"
        case .resolvedOK:     return "Okay. I'll keep watching the route."
        case .escalate:       return "Calling \(who) now. Say cancel to stop."
        case .needsAttention: return "I couldn't tell. I've left an alert on your screen."
        case .cancelled:      return "Cancelled. Nothing was sent."
        case .idle:           return ""
        }
    }
    private func reprompt(_ s: DState) -> String {
        s == .askedWellbeing
            ? "Sorry, I didn't catch that. Are you okay? Yes or no."
            : "I need a clear answer. Should I call \(who)? Yes or no."
    }

    // MARK: transitions

    @discardableResult
    func start(trigger: String = "offpath") -> String {
        state = .askedWellbeing; reprompts = 0; turns = []
        say = prompt(.askedWellbeing)
        return say
    }

    @discardableResult
    func hear(_ transcript: String, confidence: Double) -> String {
        guard !state.terminal else { return "" }
        let before = state
        let intent = Self.classify(transcript, confidence)

        if intent == .cancel {
            state = .cancelled
            return record(before, transcript, confidence, intent, prompt(.cancelled))
        }

        switch state {
        case .askedWellbeing:
            if intent == .yes { state = .resolvedOK }
            else if intent == .no || intent == .help {
                // Explicit distress does NOT skip the gate — the confirmation is what
                // protects against a single misheard word.
                state = .askedEscalate; reprompts = 0
            } else { return doReprompt(before, transcript, confidence, intent) }

        case .askedEscalate:
            if intent == .yes || intent == .help { state = .escalate }
            else if intent == .no { state = .resolvedOK }
            else { return doReprompt(before, transcript, confidence, intent) }

        default: return ""
        }
        return record(before, transcript, confidence, intent, prompt(state))
    }

    @discardableResult
    func cancel() -> String {
        let before = state
        state = .cancelled
        return record(before, "[cancel]", 1.0, .cancel, prompt(.cancelled))
    }

    private func doReprompt(_ before: DState, _ t: String, _ c: Double, _ i: DIntent) -> String {
        if reprompts >= Self.maxReprompts {
            state = .needsAttention
            return record(before, t, c, i, prompt(.needsAttention))
        }
        reprompts += 1
        return record(before, t, c, i, reprompt(before))
    }

    @discardableResult
    private func record(_ from: DState, _ heard: String, _ conf: Double,
                        _ intent: DIntent, _ said: String) -> String {
        turns.append(DTurn(from: from, to: state, heard: heard,
                           confidence: conf, intent: intent, said: said))
        say = said
        return said
    }
}
