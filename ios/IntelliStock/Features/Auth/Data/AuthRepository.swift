import Foundation

/// Thin data layer for authentication endpoints, ported from
/// features/auth/data/auth_repository.dart. Network errors surface as
/// `ApiError` from `ApiClient`.
nonisolated struct AuthRepository: Sendable {
    let client: ApiClient

    /// POST /auth/login — returns the raw `{access_token, user}` map.
    func login(_ username: String, _ password: String) async throws -> [String: JSON] {
        let data = try await client.post(
            "/auth/login",
            body: ["username": .string(username), "password": .string(password)]
        )
        return data.objectValue
    }

    /// GET /auth/me — returns the current user document.
    func fetchMe() async throws -> [String: JSON] {
        try await client.get("/auth/me").objectValue
    }
}
