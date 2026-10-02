import Foundation

/// Data access for /notification-preferences + /notifications/test, ported
/// from features/settings/data/notification_prefs_repository.dart.
nonisolated struct NotificationPrefsRepository: Sendable {
    let client: ApiClient

    /// GET /notification-preferences
    func get() async throws -> NotificationPrefs {
        NotificationPrefs(json: try await client.get("/notification-preferences"))
    }

    /// PUT /notification-preferences (replace the full matrix).
    func save(_ prefs: NotificationPrefs) async throws -> NotificationPrefs {
        NotificationPrefs(json: try await client.put("/notification-preferences", body: prefs.toJSON()))
    }

    /// POST /notifications/test — send a sample notification via `channel`
    /// so the operator can confirm the delivery option works.
    func sendTest(_ channel: NotifChannel) async throws -> JSONObject {
        try await client.post(
            "/notifications/test",
            body: ["channel": .string(channel == .discord ? "discord" : "push")]
        ).orderedObjectValue
    }
}
