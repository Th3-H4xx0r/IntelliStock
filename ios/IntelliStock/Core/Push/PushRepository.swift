import Foundation

/// Data access for `/push/devices` (APNs device-token registration) —
/// `PushRepository` in `push_repository.dart`.
nonisolated struct PushRepository: Sendable {
    let client: ApiClient

    /// `GET /push/devices` — the current user's registered devices.
    func listDevices() async throws -> [PushDevice] {
        let data = try await client.get("/push/devices")
        return data["devices"].arrayValue.filter { $0.object != nil }.map(PushDevice.init(json:))
    }

    /// `POST /push/devices` — registers or refreshes this device's token.
    func registerToken(_ token: String, env: String, appVersion: String? = nil) async throws {
        var body: [String: JSON] = [
            "device_token": .string(token),
            "platform": "ios",
            "env": .string(env),
        ]
        if let appVersion { body["app_version"] = .string(appVersion) }
        _ = try await client.post("/push/devices", body: .object(body))
    }

    /// `DELETE /push/devices/{token}` — unregisters (logout).
    func unregister(_ token: String) async throws {
        _ = try await client.delete("/push/devices/\(token)")
    }
}
