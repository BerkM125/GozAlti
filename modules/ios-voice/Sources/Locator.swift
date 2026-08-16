import CoreLocation
import Foundation

/// A location fix, or an honest admission that we do not have one.
///
/// "Feed them information about my current situation" is mostly this. A call that says
/// someone is in trouble but not where is close to useless; a map pin is actionable.
///
/// We ask for When-In-Use, not Always. Always would let us watch the route in the
/// background, which the real product wants — but it is a much bigger permission ask and
/// nothing in this app yet earns it.
@MainActor
final class Locator: NSObject, ObservableObject {
    @Published private(set) var fix: Fix?
    @Published private(set) var address: String?
    @Published private(set) var problem: String?

    private let manager = CLLocationManager()
    private let geocoder = CLGeocoder()
    private var fixedAt: Date?
    private var lastGeocodedAt: Date?

    /// A street address is what a person can act on; latitude and longitude are what a
    /// person reads back to you incorrectly. Apple's geocoder is rate limited and will
    /// start failing if hit per-update, so this runs at most once every 20 seconds.
    private func geocode(_ l: CLLocation) {
        if let last = lastGeocodedAt, Date().timeIntervalSince(last) < 20 { return }
        lastGeocodedAt = Date()
        geocoder.reverseGeocodeLocation(l) { [weak self] marks, _ in
            guard let m = marks?.first else { return }
            // Only the parts we actually got. A missing street is left out rather than
            // filled in with the city, which would read as more precision than we have.
            let parts = [[m.subThoroughfare, m.thoroughfare].compactMap { $0 }.joined(separator: " "),
                         m.locality, m.administrativeArea]
                .compactMap { $0 }.filter { !$0.isEmpty }
            Task { @MainActor in self?.address = parts.joined(separator: ", ") }
        }
    }

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyNearestTenMeters
    }

    func start() {
        switch manager.authorizationStatus {
        case .notDetermined: manager.requestWhenInUseAuthorization()
        case .denied, .restricted:
            problem = "Location denied — the message will say so instead of guessing."
        default: manager.startUpdatingLocation()
        }
    }

    /// The fix with its age computed at read time, so a stale one cannot masquerade as
    /// current just because we cached the string earlier.
    var current: Fix? {
        guard let fix, let fixedAt else { return nil }
        return Fix(lat: fix.lat, lon: fix.lon, accuracy: fix.accuracy,
                   age: Date().timeIntervalSince(fixedAt))
    }
}

extension Locator: CLLocationManagerDelegate {
    nonisolated func locationManager(_ m: CLLocationManager, didUpdateLocations locs: [CLLocation]) {
        guard let l = locs.last else { return }
        let lat = l.coordinate.latitude, lon = l.coordinate.longitude
        let acc = l.horizontalAccuracy, at = l.timestamp
        Task { @MainActor in
            self.fix = Fix(lat: lat, lon: lon, accuracy: acc,
                           age: Date().timeIntervalSince(at))
            self.fixedAt = at
            self.problem = nil
            self.geocode(l)
        }
    }

    nonisolated func locationManagerDidChangeAuthorization(_ m: CLLocationManager) {
        let status = m.authorizationStatus
        Task { @MainActor in
            switch status {
            case .authorizedWhenInUse, .authorizedAlways:
                self.problem = nil
                m.startUpdatingLocation()
            case .denied, .restricted:
                self.problem = "Location denied — the message will say so instead of guessing."
            default: break
            }
        }
    }

    nonisolated func locationManager(_ m: CLLocationManager, didFailWithError error: Error) {
        Task { @MainActor in self.problem = "No location fix: \(error.localizedDescription)" }
    }
}
