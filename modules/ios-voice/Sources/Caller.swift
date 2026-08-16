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
    /// Set when the server placed the call but could not send the text. Twilio trial
    /// accounts are limited to predefined SMS templates (error 572006), so free-text
    /// SMS fails there even though the voice call succeeds. The phone then offers the
    /// compose sheet — one tap, rather than a promise of an address that never arrives.
    @Published var needsManualText = false

    /// Backend that rings the contact and speaks the situation — modules/calling.
    /// Defaults to the box this build was made on; editable in the UI when that changes.
    /// Empty = fall back to the dialer, one tap.
    @AppStorage("callServerURL") var callServerURL = Caller.defaultServer
    static let defaultServer = "http://172.16.95.111:8060/alert"
    var hasServer: Bool { !callServerURL.trimmingCharacters(in: .whitespaces).isEmpty }

    /// Ring the contact. The server does it unattended if one is configured; otherwise
    /// the dialer, which costs one tap.
    func place(fix: Fix?, address: String?) -> String {
        let digits = number.filter { $0.isNumber || $0 == "+" }
        // The server knows the destination from CALLING_CONTACT_NUMBER, so an empty
        // field here is fine — nobody should have to type a phone number while in
        // trouble. Only the dialer path genuinely needs one.
        if hasServer {
            Task { await placeViaServer(digits, fix: fix, address: address) }
            return "Asking the server to call \(contact)…"
        }
        guard !digits.isEmpty else {
            lastError = "No number set for \(contact), and no alert service configured."
            return lastError!
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
    private func placeViaServer(_ digits: String, fix: Fix?, address: String?) async {
        guard let url = URL(string: callServerURL) else {
            lastError = "Bad call-server URL"; return
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 15
        // Two bodies: what the call says, and what the text carries. The voice promises
        // the address; "sms" is the promise being kept.
        var payload: [String: Any] = [
            "contact": contact,
            "message": Situation.spoken(contact: contact, fix: fix, address: address),
            "sms": Situation.texted(contact: contact, fix: fix, address: address),
        ]
        // Omit "to" entirely when unset so the server falls back to its configured
        // contact rather than receiving an empty string and dialing nothing.
        if !digits.isEmpty { payload["to"] = digits }
        req.httpBody = try? JSONSerialization.data(withJSONObject: payload)
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            if (200..<300).contains(code) {
                // Report which channels actually landed. "Reached" and "attempted" are
                // different facts and the user was just told someone is being called.
                let channels = (try? JSONSerialization.jsonObject(with: data))
                    .flatMap { ($0 as? [String: Any])?["channels"] as? [String: Any] } ?? [:]
                func status(_ k: String) -> String {
                    ((channels[k] as? [String: Any])?["status"] as? String) ?? "absent"
                }
                let sent = channels.keys.filter { status($0) == "sent" }.sorted()
                // The call promised an address by text. If the text did not go, the
                // promise is outstanding and the user has to know before they walk on.
                needsManualText = status("twilio") == "sent" && status("twilio_sms") != "sent"
                lastOutcome = sent.isEmpty
                    ? "Server accepted, but no channel reported success"
                    : "Reached \(contact) via " + sent.joined(separator: ", ")
                lastError = nil
            } else {
                lastError = "Call server returned \(code): "
                    + (String(data: data, encoding: .utf8)?.prefix(120) ?? "")
            }
        } catch {
            // Loud, never silent: the user was just told someone is being called.
            lastError = "Call server unreachable — \(error.localizedDescription). "
                      + (digits.isEmpty ? "No number to fall back to."
                                        : "Falling back to the dialer.")
            if !digits.isEmpty, let url = URL(string: "tel://\(digits)") {
                await UIApplication.shared.open(url)
            }
        }
    }
}
