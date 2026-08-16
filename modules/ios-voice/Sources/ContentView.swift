import MessageUI
import SwiftUI

@main
struct GozAltiVoiceApp: App {
    var body: some Scene { WindowGroup { ContentView() } }
}

struct ContentView: View {
    @StateObject private var speech = Speech()
    @StateObject private var dlg = LocalDialogue()
    @StateObject private var caller = Caller()
    @StateObject private var msg = Messenger()
    @StateObject private var loc = Locator()
    @State private var status = "Name a contact, then press a trigger."
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
                    if dlg.escalated { escalationActions }
                    transcript
                    footer
                }
                .padding(16)
            }
        }
        .preferredColorScheme(.dark)
        .sheet(isPresented: $msg.showing) {
            MessageSheet(recipient: caller.number, body: msg.body) { result in
                msg.finish(result)
            }
        }
        .task {
            dlg.contact = savedContact
            caller.number = savedNumber
            caller.contact = savedContact
            // @AppStorage only applies its default when the key is ABSENT. Earlier builds
            // wrote an empty string here, so the new default was ignored and the app
            // silently fell back to the dialer instead of calling the server. Heal it.
            if caller.callServerURL.trimmingCharacters(in: .whitespaces).isEmpty {
                caller.callServerURL = Caller.defaultServer
            }
            loc.start()
            await speech.requestPermissions()
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("GÖZALTI · VOICE").font(.system(size: 13, weight: .semibold, design: .monospaced))
                .kerning(2).foregroundStyle(go)
            Text(dlg.state.rawValue.replacingOccurrences(of: "_", with: " "))
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(dlg.escalated ? bad : .secondary)
        }
    }

    private var settings: some View {
        VStack(spacing: 8) {
        HStack(spacing: 8) {
            field("who to call", text: Binding(
                get: { dlg.contact },
                set: { dlg.contact = $0; caller.contact = $0; savedContact = $0 }))
            field("number", text: Binding(
                get: { caller.number },
                set: { caller.number = $0; savedNumber = $0 }), keyboard: .phonePad)
        }
        // Optional. Set it and modules/calling on the Acer box fans the alert out to
        // every channel at once — the only way anything actually rings unattended.
        field("alert service (optional) e.g. http://10.0.0.5:8060/alert",
              text: $caller.callServerURL)
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
            ForEach([speech.permissionProblem, loc.problem, caller.lastError].compactMap { $0 }, id: \.self) { p in
                Text(p).font(.system(size: 11, design: .monospaced)).foregroundStyle(bad)
            }
            ForEach([caller.lastOutcome, msg.outcome].filter { !$0.isEmpty }, id: \.self) { o in
                Text(o).font(.system(size: 11, design: .monospaced)).foregroundStyle(go)
            }
        }
    }

    private var micButton: some View {
        Button {
            if dlg.state.awaitingAnswer { startListening() }
        } label: {
            Text(speech.listening ? "listening — just answer"
                 : speech.speaking ? "speaking…" : "tap to answer")
                .font(.system(size: 17, weight: .semibold))
                .frame(maxWidth: .infinity).padding(.vertical, 18)
                .background(speech.listening ? bad : go, in: RoundedRectangle(cornerRadius: 12))
                .foregroundStyle(Color.black)
        }
        .disabled(!dlg.state.awaitingAnswer)
        .opacity(dlg.state.awaitingAnswer ? 1 : 0.4)
    }

    private var triggers: some View {
        HStack(spacing: 8) {
            btn("simulate off-path") { begin("offpath") }
            btn("keyword trigger") { begin("keyword") }
        }
    }

    private var answers: some View {
        HStack(spacing: 8) {
            btn("“yes”") { answer("yes", 0.95) }.disabled(!dlg.state.awaitingAnswer)
            btn("“no”") { answer("no", 0.95) }.disabled(!dlg.state.awaitingAnswer)
            btn("cancel", tint: bad) { speech.stopListening(); dlg.cancel(); speak() }
        }
    }

    /// Both channels stay reachable after escalation. The text is the default because it
    /// carries the map link; the call is there because a ringing phone gets attention a
    /// notification does not.
    private var escalationActions: some View {
        HStack(spacing: 8) {
            btn("text \(dlg.contact)") {
                msg.compose(contact: dlg.contact, fix: loc.current, address: loc.address)
            }
            btn("call \(dlg.contact)") {
                status = caller.place(fix: loc.current, address: loc.address)
            }
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
                ForEach(dlg.turns) { t in
                    Text("\(t.from.rawValue) → \(t.to.rawValue)   "
                         + "“\(t.heard)” \(String(format: "%.2f", t.confidence)) [\(t.intent.rawValue)]")
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
            if let f = loc.current {
                Text(String(format: "fix: %.5f, %.5f  ±%.0fm", f.lat, f.lon, f.accuracy))
                Text("addr: \(loc.address ?? "resolving…")")
            } else {
                Text("fix: none — the message will say so")
            }
            Text("alert service: \(caller.hasServer ? caller.callServerURL : "NOT SET — dialer only")")
                .foregroundStyle(caller.hasServer ? go : .orange)
            Text("The decision is made on this phone by a deterministic state machine, never by a language model. Two explicit answers are required; silence never escalates; cancel always wins. We contact the person named above — never emergency services. iOS requires you to press Send; no app may text or dial on your behalf unattended.")
                .padding(.top, 4)
        }
        .font(.system(size: 10, design: .monospaced))
        .foregroundStyle(.secondary)
    }

    // MARK: flow

    private func begin(_ trigger: String) {
        msg.outcome = ""
        dlg.start(trigger: trigger)
        speak()
    }

    private func answer(_ text: String, _ conf: Double) {
        dlg.hear(text, confidence: conf)
        speak()
    }

    /// Speak the prompt, and only when it finishes open the mic — otherwise the
    /// recogniser transcribes our own voice. On escalation, stage the text: it carries
    /// the map link, and the user only has to press Send.
    private func speak() {
        let line = dlg.say
        speech.say(line) {
            if dlg.escalated {
                // Twilio places the call AND sends the text, both unattended. The compose
                // sheet is no longer opened automatically — it would demand a tap for a
                // message that has already gone out, and a second copy is worse than none.
                // It stays available as a button if the server is unreachable.
                status = caller.place(fix: loc.current, address: loc.address)
            } else if dlg.state.awaitingAnswer {
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
