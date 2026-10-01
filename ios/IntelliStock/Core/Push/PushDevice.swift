import Foundation

/// A registered APNs device, as `GET /push/devices` returns it —
/// `PushDevice` in `push_device.dart`.
nonisolated struct PushDevice: Identifiable, Hashable, Sendable {
    let deviceToken: String
    let platform: String
    let env: String
    let appVersion: String?
    let lastSeen: String?

    var id: String { deviceToken }

    init(deviceToken: String, platform: String, env: String, appVersion: String? = nil, lastSeen: String? = nil) {
        self.deviceToken = deviceToken
        self.platform = platform
        self.env = env
        self.appVersion = appVersion
        self.lastSeen = lastSeen
    }

    init(json j: JSON) {
        deviceToken = j["device_token"].stringOr("")
        platform = j["platform"].stringOr("ios")
        env = j["env"].stringOr("prod")
        appVersion = j["app_version"].string
        lastSeen = j["last_seen"].string
    }

    /// Short, human-friendly identifier (APNs tokens are 64 hex chars).
    var tokenSuffix: String {
        deviceToken.count <= 8 ? deviceToken : "…" + deviceToken.suffix(8)
    }
}
