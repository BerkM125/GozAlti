import Foundation
import SwiftUI
import UIKit

/// Placing the call, and being honest about what iOS allows.
///
/// CAN THE SERVER HOOK GO THROUGH APPLE'S CALL FEATURE?
/// ----------------------------------------------------
/// No, and the reason is worth writing down because it looks like it should work.
///
/// Everything Apple exposes for calling is *outbound from this app on this device*:
///
///  * `tel:` / `facetime-audio:` — open a confirmation sheet. Local only. A push
///    notification cannot open one; URL schemes need a foreground user gesture.
///  * CallKit — reports and manages calls belonging to *our own VoIP service*. It cannot
///    originate a cellular call, and there is no VoIP service behind this app.
///  * SiriKit `INStartCallIntent` — same story: our service, and donating an intent
///    describes an action rather than performing one.
///  * Shortcuts' Call action — still confirms when triggered from an app.
///
/// So a backend can never reach in and make this phone dial. Siri does it with private
/// entitlements that are not requestable. Apple's own Check In and Emergency SOS are
/// system processes, not apps, which is exactly why they can do what we cannot.
///
/// That leaves two real options, and they split cleanly:
///
///  1. ONE TAP, FREE, NOW — `tel:` for the call, MessageUI for the text. The tap is also
///     a second confirmation, after the voice one.
///  2. ZERO TAP — a backend places the call itself (Twilio and friends) and reads the
///     report aloud. Strictly better for the case that matters, because it still works
///     when the phone is broken, taken, or dead. `callServerURL` points at that backend.
///
/// Unset, we fall back to the dialer and say so rather than pretending a call went out.
/// Per modules/offpath-911's binding rules this only ever contacts a nominated person.
/// Emergency services are never dialed from this codebase.
@MainActor
final class Caller: ObservableObject {
    @Published var contact = "Dhruv"
    @Published var number = ""
    @Published var lastError: String?
    @Published var lastOutcome = ""

    /// Optional backend that rings the contact and speaks the situation — modules/calling
    /// on the Acer box, `http://<box>:8060/alert`. Empty = dialer, one tap.
    @AppStorage("callServerURL") var callServerURL = ""
    var hasServer: Bool { !callServerURL.trimmingCharacters(in: .whitespaces).isEmpty }

    /// One tap: hands the number to the Phone app, user connects.
    func place(fix: Fix?) -> String {
        let digits = number.filter { $0.isNumber || $0 == "+" }
        guard !digits.isEmpty else {
            lastError = "No number set for \(contact)."
            return lastError!
        }
        if !callServerURL.isEmpty {
            Task { await placeViaServer(digits, fix: fix) }
            return "Asking the server to call \(contact)…"
        }
        guard let url = URL(string: "tel://\(digits)"),
              UIApplication.shared.canOpenURL(url) else {
            lastError = "This device cannot place calls (the simulator has no dialer)."
            return lastError!
        }
        UIApplication.shared.open(url)
        lastOutcome = "Dialer opened for \(contact)"
        return "Opening the dialer for \(contact) — tap call to connect."
    }

    /// Zero-tap: the backend rings the contact and reads the situation aloud.
    private func placeViaServer(_ digits: String, fix: Fix?) async {
        guard let url = URL(string: callServerURL) else {
            lastError = "Bad call-server URL"; return
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 15
        req.httpBody = try? JSONSerialization.data(withJSONObject: [
            "to": digits, "contact": contact,
            "message": Situation.report(contact: contact, fix: fix, spoken: true),
        ])
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            if (200..<300).contains(code) {
                lastOutcome = "Server placed the call to \(contact)"
                lastError = nil
            } else {
                lastError = "Call server returned \(code): "
                    + (String(data: data, encoding: .utf8)?.prefix(120) ?? "")
            }
        } catch {
            // Loud, never silent: the user was just told someone is being called.
            lastError = "Call server unreachable — \(error.localizedDescription). "
                      + "Falling back to the dialer."
            if let url = URL(string: "tel://\(digits)") {
                await UIApplication.shared.open(url)
            }
        }
    }
}
