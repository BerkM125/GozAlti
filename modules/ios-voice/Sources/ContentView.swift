import SwiftUI

@main
struct GozAltiVoiceApp: App {
    var body: some Scene { WindowGroup { ContentView() } }
}

struct ContentView: View {
    @StateObject private var speech = Speech()
    @StateObject private var dlg = DialogueClient()
    @State private var status = "Set your laptop's address, then press a trigger."
    @AppStorage("baseURL") private var savedURL = "http://172.16.95.111:8050"
    @AppStorage("contact") private var savedContact = "Dhruv"
    @AppStorage("number") private var savedNumber = ""

    private let ink = Color(red: 0.90, green: 0.92, blue: 0.94)
    private let go = Color(red: 0.24, green: 0.86, blue: 0.52)
    private let bad = Color(red: 0.91, green: 0.27, blue: 0.23)

    var body: some View {
        ZStack {
            Color(red: 0.05, green: 0.06, blue: 0.09).ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    header
                    settings
                    saidCard
                    heardLine
                    micButton
                    triggers
                    answers
                    transcript
                    footer
                }
                .padding(16)
            }
        }
        .preferredColorScheme(.dark)
        .task {
            dlg.baseURL = savedURL
            dlg.contact = savedContact
            dlg.contactNumber = savedNumber
            await speech.requestPermissions()
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("GÖZALTI · VOICE").font(.system(size: 13, weight: .semibold, design: .monospaced))
                .kerning(2).foregroundStyle(go)
            Text(dlg.state.replacingOccurrences(of: "_", with: " "))
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(dlg.escalated ? bad : .secondary)
        }
    }

    private var settings: some View {
        VStack(spacing: 8) {
            field("laptop address", text: Binding(
                get: { dlg.baseURL },
                set: { dlg.baseURL = $0; savedURL = $0 }))
            HStack(spacing: 8) {
                field("who to call", text: Binding(
                    get: { dlg.contact }, set: { dlg.contact = $0; savedContact = $0 }))
                field("number", text: Binding(
                    get: { dlg.contactNumber },
                    set: { dlg.contactNumber = $0; savedNumber = $0 }), keyboard: .phonePad)
            }
        }
    }

    private func field(_ label: String, text: Binding<String>,
                       keyboard: UIKeyboardType = .URL) -> some View {
        TextField(label, text: text)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .keyboardType(keyboard)
            .font(.system(size: 14, design: .monospaced))
            .padding(12)
            .background(Color.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 10))
            .foregroundStyle(ink)
    }

    private var saidCard: some View {
        Text(dlg.say.isEmpty ? status : dlg.say)
            .font(.system(size: 18))
            .foregroundStyle(ink)
            .frame(maxWidth: .infinity, minHeight: 70, alignment: .leading)
            .padding(14)
            .background(Color.white.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
            .overlay(Rectangle().frame(width: 3).foregroundStyle(go), alignment: .leading)
            .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private var heardLine: some View {
        VStack(alignment: .leading, spacing: 3) {
            if !speech.heard.isEmpty {
                Text("heard: \(speech.heard)  (\(String(format: "%.2f", speech.confidence)))")
                    .font(.system(size: 12, design: .monospaced)).foregroundStyle(.secondary)
            }
            if let p = speech.permissionProblem {
                Text(p).font(.system(size: 12, design: .monospaced)).foregroundStyle(bad)
            }
            if let e = dlg.lastError {
                Text(e).font(.system(size: 11, design: .monospaced)).foregroundStyle(bad)
            }
        }
    }

    private var micButton: some View {
        Button {
            if dlg.awaitingAnswer { startListening() }
        } label: {
            Text(speech.listening ? "listening — just answer"
                 : speech.speaking ? "speaking…" : "tap to answer")
                .font(.system(size: 17, weight: .semibold))
                .frame(maxWidth: .infinity).padding(.vertical, 18)
                .background(speech.listening ? bad : go, in: RoundedRectangle(cornerRadius: 12))
                .foregroundStyle(Color.black)
        }
        .disabled(!dlg.awaitingAnswer)
        .opacity(dlg.awaitingAnswer ? 1 : 0.4)
    }

    private var triggers: some View {
        HStack(spacing: 8) {
            btn("simulate off-path") { Task { await begin("offpath") } }
            btn("keyword trigger") { Task { await begin("keyword") } }
        }
    }

    private var answers: some View {
        HStack(spacing: 8) {
            btn("“yes”") { Task { await answer("yes", 0.95) } }.disabled(!dlg.awaitingAnswer)
            btn("“no”") { Task { await answer("no", 0.95) } }.disabled(!dlg.awaitingAnswer)
            btn("cancel", tint: bad) { Task { await dlg.cancel(); speech.stopListening() } }
        }
    }

    private func btn(_ title: String, tint: Color? = nil,
                     action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title).font(.system(size: 14))
                .frame(maxWidth: .infinity).padding(.vertical, 14)
                .background(Color.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 10))
                .foregroundStyle(tint ?? ink)
        }
    }

    private var transcript: some View {
        VStack(alignment: .leading, spacing: 4) {
            if !dlg.turns.isEmpty {
                Text("TRANSCRIPT").font(.system(size: 10, design: .monospaced))
                    .kerning(1.5).foregroundStyle(.secondary)
                ForEach(Array(dlg.turns.enumerated()), id: \.offset) { _, t in
                    Text("\(t["from"] as? String ?? "") → \(t["to"] as? String ?? "")   "
                         + "“\(t["heard"] as? String ?? "")”  [\(t["intent"] as? String ?? "")]")
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text("recognition: \(speech.onDevice ? "on-device (audio never leaves this phone)" : "server-backed")")
                .foregroundStyle(speech.onDevice ? go : .orange)
            Text("voice: \(speech.voiceName)")
            Text("The decision is made by the state machine on your laptop, never by a language model. Two explicit answers are required; silence never escalates; cancel always wins. We call the person named above — never emergency services.")
                .padding(.top, 4)
        }
        .font(.system(size: 10, design: .monospaced))
        .foregroundStyle(.secondary)
    }

    // MARK: flow

    private func begin(_ trigger: String) async {
        await dlg.start(trigger: trigger)
        speak()
    }

    private func answer(_ text: String, _ conf: Double) async {
        await dlg.hear(text, confidence: conf)
        speak()
    }

    /// Speak the prompt, and only when it finishes open the mic — otherwise the
    /// recogniser transcribes our own voice. On escalation, hand off to the dialer.
    private func speak() {
        let line = dlg.say
        speech.say(line) {
            if dlg.escalated {
                status = dlg.placeCall()
            } else if dlg.awaitingAnswer {
                startListening()
            }
        }
    }

    private func startListening() {
        speech.listen { transcript, confidence in
            Task { await answer(transcript, confidence) }
        }
    }
}
