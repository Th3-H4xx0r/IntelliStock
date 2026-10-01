import Foundation
import Observation

/// The JWT and cached user, persisted in the keychain — `SessionStore` in
/// `session.dart`.
///
/// The token lives in memory so `ApiClient` reads it on every request. Every
/// change except `setUser` re-mirrors the API base and token into the App
/// Group, so the home-screen widget can self-refresh while the app is closed.
@Observable
final class SessionStore: ApiTokenSource {
    static let tokenKey = "intellistock_token"
    static let userKey = "intellistock_user"

    private(set) var token: String?
    /// The cached user object (`Map<String, dynamic>` in Dart).
    private(set) var user: JSON?

    var isAuthenticated: Bool {
        guard let token else { return false }
        return !token.isEmpty
    }

    var hasCompletedOnboarding: Bool {
        user?["has_completed_onboarding"].bool ?? false
    }

    /// `(user?['username'] ?? user?['name'] ?? 'User').toString()`.
    var username: String {
        user?["username"].string ?? user?["name"].string ?? "User"
    }

    @ObservationIgnored private let storage: any SecureStorage
    @ObservationIgnored private let widgetSync: WidgetSync
    /// Live getter for the active API base URL (mirrored to the widget).
    @ObservationIgnored private let apiBaseUrl: () -> String

    init(storage: any SecureStorage, widgetSync: WidgetSync, apiBaseUrl: @escaping () -> String = { "" }) {
        self.storage = storage
        self.widgetSync = widgetSync
        self.apiBaseUrl = apiBaseUrl
    }

    /// Loads the persisted session. Synchronous, so the first frame already
    /// knows whether someone is signed in (main.dart read it before runApp).
    func load() {
        token = storage.read(Self.tokenKey)
        if let raw = storage.read(Self.userKey) {
            let decoded = try? JSON(data: Data(raw.utf8))
            user = decoded?.object != nil ? decoded : nil
        }
        syncWidgetCredentials()
    }

    /// Stores a fresh sign-in. Throws when the keychain refuses the token, so
    /// the login screen can report it.
    func setSession(token: String, user: JSON?) async throws {
        self.token = token
        self.user = user
        try storage.write(Self.tokenKey, token)
        if let user {
            try storage.write(Self.userKey, Self.encode(user))
        } else {
            storage.delete(Self.userKey)
        }
        syncWidgetCredentials()
    }

    /// Replaces just the JWT (a sliding-renewal token from
    /// `x-refreshed-token`), keeping the cached user. No-op when empty or
    /// unchanged. Persistence is best-effort: a locked keychain never throws
    /// out of this fire-and-forget path.
    func setToken(_ token: String) async {
        if token.isEmpty || token == self.token { return }
        self.token = token
        try? storage.write(Self.tokenKey, token)
        syncWidgetCredentials()
    }

    func setUser(_ user: JSON) async throws {
        self.user = user
        try storage.write(Self.userKey, Self.encode(user))
    }

    /// Signs out: forgets both keys and clears the widget's token.
    func clear() async {
        token = nil
        user = nil
        storage.delete(Self.tokenKey)
        storage.delete(Self.userKey)
        syncWidgetCredentials()
    }

    private func syncWidgetCredentials() {
        widgetSync.syncCredentials(apiBase: apiBaseUrl(), token: token ?? "")
    }

    private static func encode(_ user: JSON) -> String {
        guard let data = try? user.data() else { return "{}" }
        return String(decoding: data, as: UTF8.self)
    }
}
