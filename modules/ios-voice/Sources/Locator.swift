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
    @Published private(set) var problem: String?

    private let manager = CLLocationManager()
    private var fixedAt: Date?

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
