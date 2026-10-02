import Foundation
import Observation

/// The per-category notification matrix with optimistic toggles —
/// `NotificationPrefsController` (auto-disposed, so the screen owns it).
@Observable
final class NotificationPrefsModel {
    private(set) var prefs: Loadable<NotificationPrefs> = .loading

    @ObservationIgnored private let repository: () -> NotificationPrefsRepository

    init(repository: @escaping () -> NotificationPrefsRepository) {
        self.repository = repository
    }

    /// The first load (Dart's `build`).
    func load() async {
        let result = await Loadable.capture { try await self.repository().get() }
        if case .failed(let error) = result, error is CancellationError { return }
        prefs = result
    }

    /// Flips one channel of one category: updates at once, persists the full
    /// matrix, reverts on failure. Returns an error string on failure, nil on
    /// success (the screen shows it).
    func toggle(_ category: String, _ channel: NotifChannel, _ value: Bool) async -> String? {
        guard let current = prefs.value else { return "Preferences not loaded yet" }
        let route = current.routeFor(category)
        let updated = channel == .discord ? route.copyWith(discord: value) : route.copyWith(push: value)
        let optimistic = current.withRoute(category, updated)

        prefs = .loaded(optimistic)
        do {
            prefs = .loaded(try await repository().save(optimistic))
            return nil
        } catch {
            prefs = .loaded(current)
            return settingsErrorText(error)
        }
    }

    /// Sends a test notification via `channel`; returns the backend's result.
    func sendTest(_ channel: NotifChannel) async throws -> JSONObject {
        try await repository().sendTest(channel)
    }

    /// The rows to show: the API taxonomy grouped in first-appearance order,
    /// else the built-in categories under `Notifications`.
    nonisolated static func groupedTypes(_ prefs: NotificationPrefs) -> [(group: String, types: [NotificationType])] {
        let types = prefs.types.isEmpty
            ? kNotificationCategories.map { NotificationType(key: $0.key, group: "Notifications", label: $0.label, desc: $0.description) }
            : prefs.types
        var groups: [String] = []
        for t in types where !groups.contains(t.group) {
            groups.append(t.group)
        }
        return groups.map { group in (group, types.filter { $0.group == group }) }
    }
}

/// The toast after a test send — `_sendTest`'s message logic.
nonisolated struct NotificationTestOutcome: Equatable, Sendable {
    let message: String
    let ok: Bool

    static func label(_ channel: NotifChannel) -> String {
        channel == .discord ? "Discord" : "iOS push"
    }

    init(message: String, ok: Bool) {
        self.message = message
        self.ok = ok
    }

    /// From the backend's result map.
    init(channel: NotifChannel, result: JSONObject) {
        let json = JSON.object(result)
        let ok = json["ok"].bool
        let label = Self.label(channel)
        if channel == .push && !ok {
            let devices = json["devices"].or(0)
            if devices == .int(0) || devices == .double(0) {
                self.init(message: "No iOS device registered yet — tap \"Enable push on this device\".", ok: false)
            } else {
                let errors = json["errors"].arrayValue
                let reason = errors.first.map { $0["reason"].or("").dartDescription } ?? ""
                self.init(
                    message: reason.isEmpty ? "Push not delivered — check APNs setup." : "Push failed: \(reason)",
                    ok: false
                )
            }
        } else {
            self.init(message: ok ? "\(label) test sent ✓" : "\(label) test could not be sent", ok: ok)
        }
    }

    /// A send that threw.
    init(channel: NotifChannel, error: any Error) {
        self.init(message: "\(Self.label(channel)) test failed: \(settingsErrorText(error))", ok: false)
    }
}

/// A registered device's subtitle — `_DeviceTile`: `IOS · env · seen <date>`.
nonisolated func pushDeviceSubtitle(_ device: PushDevice) -> String {
    // "iOS" in its own casing (the redesign bans upper case except acronyms);
    // any other platform is capitalised ("android" → "Android").
    let platform = device.platform.lowercased() == "ios" ? "iOS" : device.platform.capitalized
    var parts = [platform, device.env]
    if let seen = device.lastSeen, !seen.isEmpty {
        parts.append("seen \(seen.split(separator: "T", omittingEmptySubsequences: false).first.map(String.init) ?? seen)")
    }
    return parts.joined(separator: " · ")
}

/// The app's `version+build` — `appVersionProvider`.
nonisolated func appVersionString(_ info: [String: Any]? = Bundle.main.infoDictionary) -> String {
    let version = info?["CFBundleShortVersionString"] as? String ?? ""
    let build = info?["CFBundleVersion"] as? String ?? ""
    return version + (build.isEmpty ? "" : "+\(build)")
}

/// Dart's `e.toString()` for a caught error (`ApiError.toString()` is its message).
nonisolated func settingsErrorText(_ error: any Error) -> String {
    (error as? ApiError)?.message ?? error.localizedDescription
}
