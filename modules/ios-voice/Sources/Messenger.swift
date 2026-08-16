import MessageUI
import SwiftUI

/// Texting the contact, using Apple's own compose sheet.
///
/// WHY A SHEET AND NOT A SILENT SEND
/// ---------------------------------
/// iOS has no API that sends an SMS without the user seeing and sending it. `MFMessage-
/// ComposeViewController` is the sanctioned path: we supply the recipient and the body,
/// the sheet appears over our app, the user presses Send. The `sms:` URL scheme is worse —
/// it throws the user out to Messages and tells us nothing about what happened.
///
/// The sheet is not a workaround for a missing permission. Apple will not let an app send
/// messages from a person's number unattended, in the same way it will not let one dial
/// unattended, and for the same reason.
///
/// What we get in exchange is a delegate result, so the app can say "sent" only when it
/// was actually sent. Reporting a message as delivered when it was cancelled would be the
/// worst bug in this codebase.
@MainActor
final class Messenger: ObservableObject {
    @Published var showing = false
    @Published var body = ""
    @Published var outcome = ""

    static var available: Bool { MFMessageComposeViewController.canSendText() }

    /// Stage the message. Presentation happens in the view; sending happens by the user.
    func compose(contact: String, fix: Fix?) {
        body = Situation.report(contact: contact, fix: fix, spoken: false)
        guard Self.available else {
            outcome = "This device cannot send texts (no SIM, or the simulator)."
            return
        }
        showing = true
    }

    func finish(_ result: MessageComposeResult) {
        showing = false
        switch result {
        case .sent:      outcome = "Message sent."
        case .cancelled: outcome = "Message cancelled — nothing was sent."
        case .failed:    outcome = "Message failed to send."
        @unknown default: outcome = "Unknown message result."
        }
    }
}

/// Thin bridge to the UIKit compose sheet.
struct MessageSheet: UIViewControllerRepresentable {
    let recipient: String
    let body: String
    let onResult: (MessageComposeResult) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onResult: onResult) }

    func makeUIViewController(context: Context) -> MFMessageComposeViewController {
        let vc = MFMessageComposeViewController()
        vc.messageComposeDelegate = context.coordinator
        if !recipient.isEmpty { vc.recipients = [recipient] }
        vc.body = body
        return vc
    }

    func updateUIViewController(_ vc: MFMessageComposeViewController, context: Context) {}

    final class Coordinator: NSObject, MFMessageComposeViewControllerDelegate {
        let onResult: (MessageComposeResult) -> Void
        init(onResult: @escaping (MessageComposeResult) -> Void) { self.onResult = onResult }

        func messageComposeViewController(_ c: MFMessageComposeViewController,
                                          didFinishWith result: MessageComposeResult) {
            onResult(result)
        }
    }
}
