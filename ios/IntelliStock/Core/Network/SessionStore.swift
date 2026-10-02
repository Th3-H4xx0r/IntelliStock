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
    ///
    /// Returns false — changing nothing — when the keychain cannot be read
    /// yet (before first unlock): that is not "signed out". Mirrors the
    /// widget's credentials only for a real session, so a load never wipes
    /// them.
    @discardableResult
    func load() -> Bool {
        let storedToken: String?
        let rawUser: String?
        do {
            storedToken = try storage.readChecked(Self.tokenKey)
            rawUser = try storage.readChecked(Self.userKey)
        } catch {
            return false
        }
        token = storedToken
        if let rawUser {
            let decoded = try? JSON(data: Data(rawUser.utf8))
            user = decoded?.object != nil ? decoded : nil
        } else {
            user = nil
        }
        if isAuthenticated { syncWidgetCredentials() }
        return true
    }

    /// Stores a fresh sign-in. Throws when the keychain refuses the token, so
    /// the login screen can report it.
    ///
    /// Persists first, then updates memory (as `ApiBaseUrlStore.set` does),
    /// so a keychain failure never leaves a session that would not survive a
    /// relaunch.
    func setSession(token: String, user: JSON?) async throws {
        try storage.write(Self.tokenKey, token)
        if let user {
            try storage.write(Self.userKey, Self.encode(user))
        } else {
            storage.delete(Self.userKey)
        }
        self.token = token
        self.user = user
        syncWidgetCredentials()
    }

    /// Replaces just the JWT (a sliding-renewal token from
    /// `x-refreshed-token`), keeping the cached user. No-op when empty,
    /// unchanged, or signed out — a late renewal after Sign Out must not sign
    /// the old session back in. Persistence is best-effort: a locked keychain
    /// never throws out of this fire-and-forget path.
    func setToken(_ token: String) async {
        if token.isEmpty || token == self.token || self.token == nil { return }
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
