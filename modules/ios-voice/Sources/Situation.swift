import CoreLocation
import Foundation

/// What we tell the contact, and where it comes from.
///
/// Binding rule 3 of modules/offpath-911: every sentence here must trace to something we
/// actually observed. No invented colour about the user's surroundings, no "they may be
/// in danger", no guessed street name. If we have no location fix we say we have no
/// location fix — an honest gap beats a plausible fabrication when someone is deciding
/// whether to get in a car.
struct Fix {
    let lat: Double
    let lon: Double
    /// Metres of horizontal uncertainty, straight from CoreLocation.
    let accuracy: Double
    /// When the fix was taken, not when we sent the message. A four-minute-old GPS
    /// position is a materially different fact from a current one.
    let age: TimeInterval

    var mapsLink: String { String(format: "https://maps.apple.com/?ll=%.5f,%.5f", lat, lon) }
}

enum Situation {
    /// One report, two channels. Calling and texting must not tell different stories.
    static func report(contact: String, fix: Fix?, spoken: Bool) -> String {
        var lines: [String] = []
        lines.append(spoken
            ? "This is an automated message from Gozalti Safe Walk."
            : "GözAltı Safe Walk — automated message.")

        if let fix {
            lines.append(String(format: "Last known position %.5f, %.5f (±%.0f m, %@).",
                                fix.lat, fix.lon, fix.accuracy, ageWords(fix.age)))
            // Spoken aloud, a URL is noise. Texted, it is the entire point.
            if !spoken { lines.append(fix.mapsLink) }
        } else {
            lines.append("No location fix was available.")
        }

        lines.append("They left their planned route and confirmed by voice that they wanted you contacted.")
        return lines.joined(separator: spoken ? " " : "\n")
    }

    private static func ageWords(_ t: TimeInterval) -> String {
        if t < 45 { return "just now" }
        let m = Int(t / 60)
        return m < 60 ? "\(m) min old" : "over an hour old"
    }
}
