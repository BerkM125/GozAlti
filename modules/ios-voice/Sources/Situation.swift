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
    /// One event, two bodies. The call and the text must not tell different stories, but
    /// they cannot carry the same words: a URL read aloud is noise, and an address spoken
    /// once during a phone call is gone the moment it is said. So the voice promises the
    /// text, and the text is what the contact still has in five minutes.
    static func spoken(contact: String, fix: Fix?, address: String?) -> String {
        var s = ["This is an automated message from Gozalti Safe Walk.",
                 "\(contact), your friend left their planned route and confirmed by voice that they want you contacted."]
        if let address, !address.isEmpty {
            s.append("They are near \(address).")
        } else if fix != nil {
            s.append("Their exact coordinates are in the text message.")
        } else {
            s.append("No location fix was available.")
        }
        if fix != nil { s.append("Sending you their address by text now.") }
        return s.joined(separator: " ")
    }

    static func texted(contact: String, fix: Fix?, address: String?) -> String {
        var s = ["GözAltı Safe Walk — automated alert.",
                 "\(contact): your friend left their planned route and confirmed by voice that they want you contacted."]
        if let address, !address.isEmpty { s.append(address) }
        if let fix {
            s.append(String(format: "%.5f, %.5f (±%.0f m, %@)",
                            fix.lat, fix.lon, fix.accuracy, ageWords(fix.age)))
            s.append(fix.mapsLink)
        } else {
            s.append("No location fix was available.")
        }
        return s.joined(separator: "\n")
    }

    private static func ageWords(_ t: TimeInterval) -> String {
        if t < 45 { return "just now" }
        let m = Int(t / 60)
        return m < 60 ? "\(m) min old" : "over an hour old"
    }
}
