import Foundation
import SwiftUI
import UIKit

/// Placing the call, and being honest about what iOS allows.
///
/// WHY THERE IS NO ZERO-TAP DIAL
/// -----------------------------
/// Siri can place a call without confirmation. Third-party apps cannot, and this is not
/// an API we have failed to find:
///
///  * Siri runs as an Apple system process with private entitlements. There is no public
///    equivalent and none can be requested.
///  * SiriKit's `INStartCallIntent` is for VoIP apps starting calls *in their own
///    service*. Donating an intent describes an action; it does not perform one.
///  * CallKit reports and manages VoIP calls. It cannot originate a cellular call.
///  * Shortcuts' Call action still requires user confirmation when invoked from an app.
///  * `tel:` always presents the system confirmation sheet.
///
/// So the phone gets ONE tap, which is also the confirmation gate — the user has now
/// confirmed twice, once by voice and once in the dialer.
///
/// THE ZERO-TAP PATH IS SERVER-SIDE
/// --------------------------------
/// To call someone automatically *and read them the situation*, the call is placed by a
/// backend (Twilio and friends), not by this phone. That is strictly better for the case
/// that matters: it still works when the user's phone is broken, taken, or out of
/// battery. `callServerURL` points at that backend; when it is unset we fall back to the
/// dialer and say so rather than pretending a call went out.
///
/// Per modules/offpath-911's binding rules this only ever contacts a nominated person.
/// Emergency services are never dialed from this codebase.
@MainActor
final class Caller: ObservableObject {
    @Published var contact = "Dhruv"
    @Published var number = ""
    @Published var lastError: String?
    @Published var lastOutcome = ""

    /// Optional backend that places the call and speaks the situation. Empty = dialer.
    @AppStorage("callServerURL") var callServerURL = ""

    /// What we would tell the contact. Only evidence-linked facts — never improvised
    /// colour about the user's surroundings (offpath-911 binding rule 3).
    func situationReport(state: String, lat: Double? = nil, lon: Double? = nil) -> String {
        var parts = ["This is an automated call from GözAltı Safe Walk.",
                     "\(contact), your contact asked me to reach you."]
        if let lat, let lon {
            parts.append(String(format: "Their last known position is %.5f, %.5f.", lat, lon))
        } else {
            parts.append("No location fix was available.")
        }
        parts.append("They confirmed by voice that they wanted you contacted.")
        return parts.joined(separator: " ")
    }

    /// One tap: hands the number to the Phone app, user connects.
    func place() -> String {
        let digits = number.filter { $0.isNumber || $0 == "+" }
        guard !digits.isEmpty else {
            lastError = "No number set for \(contact)."
            return lastError!
        }
        if !callServerURL.isEmpty {
            Task { await placeViaServer(digits) }
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
    private func placeViaServer(_ digits: String) async {
        guard let url = URL(string: callServerURL) else {
            lastError = "Bad call-server URL"; return
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 15
        req.httpBody = try? JSONSerialization.data(withJSONObject: [
            "to": digits, "contact": contact,
            "message": situationReport(state: "escalate"),
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
