import Foundation
import Observation

/// Normalizes to a backend ORIGIN, ported from `api_base_url.dart`: trim, then
/// reduce a valid http(s) URL to `scheme://host[:port]`, dropping any path,
/// query, fragment and trailing slashes. The backend serves bare paths from
/// root (`/auth/login`), so keeping a path would silently misroute requests.
/// A non-http(s) or hostless input comes back trailing-slash-stripped so
/// `isValidBaseUrl` can still reject it.
///
/// Like Dart's `Uri`, the scheme and host are lower-cased and a default port
/// (80 for http, 443 for https) is dropped.
nonisolated func normalizeBaseUrl(_ raw: String) -> String {
    let s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    if s.isEmpty { return "" }
    if let origin = BaseUrlOrigin(s) {
        return origin.text
    }
    var stripped = s
    while stripped.hasSuffix("/") { stripped.removeLast() }
    return stripped
}

/// A syntactically usable backend base URL: http/https scheme and a non-empty
/// host.
nonisolated func isValidBaseUrl(_ raw: String) -> Bool {
    let s = normalizeBaseUrl(raw)
    if s.isEmpty { return false }
    return BaseUrlOrigin(s) != nil
}

/// The `scheme://host[:port]` origin of an http(s) URL, or nil when the text
/// is not one.
nonisolated private struct BaseUrlOrigin {
    let text: String

    init?(_ s: String) {
        guard let components = URLComponents(string: s),
              let scheme = components.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              let rawHost = components.host, !rawHost.isEmpty
        else { return nil }
        var host = rawHost.lowercased()
        if host.contains(":"), !host.hasPrefix("[") { host = "[\(host)]" }
        let defaultPort = scheme == "http" ? 80 : 443
        if let port = components.port, port != defaultPort {
            text = "\(scheme)://\(host):\(port)"
        } else {
            text = "\(scheme)://\(host)"
        }
    }
}

/// The active backend base URL, persisted in the keychain under
/// `api_base_url` — `ApiBaseUrlStore` in `api_base_url.dart`.
///
/// `AppServices` sets `onChange` so the API client is rebuilt against the new
/// origin whenever the URL changes (Dart's `dioProvider` watched this store).
@Observable
final class ApiBaseUrlStore {
    static let storageKey = "api_base_url"

    private(set) var baseUrl = ""

    var isConfigured: Bool { !baseUrl.isEmpty }

    /// The module-level `normalizeBaseUrl(_:)`, for discoverability.
    nonisolated static func normalizeBaseUrl(_ raw: String) -> String {
        IntelliStock.normalizeBaseUrl(raw)
    }

    /// The module-level `isValidBaseUrl(_:)`, for discoverability.
    nonisolated static func isValidBaseUrl(_ raw: String) -> Bool {
        IntelliStock.isValidBaseUrl(raw)
    }

    /// Called after every `load()` and `set(_:)` with the new URL.
    @ObservationIgnored var onChange: ((String) -> Void)?

    @ObservationIgnored private let storage: any SecureStorage

    init(storage: any SecureStorage) {
        self.storage = storage
    }

    /// Reads the persisted URL, normalized. Synchronous, so the very first
    /// frame already knows whether the app is configured.
    ///
    /// Returns false — changing nothing — when the keychain cannot be read
    /// yet (before first unlock): that is not "no server configured".
    @discardableResult
    func load() -> Bool {
        let stored: String?
        do {
            stored = try storage.readChecked(Self.storageKey)
        } catch {
            return false
        }
        baseUrl = Self.normalizeBaseUrl(stored ?? "")
        onChange?(baseUrl)
        return true
    }

    /// Normalizes and persists `url`; an empty value deletes the key.
    ///
    /// Persists before updating memory, so a keychain failure leaves the old
    /// URL in place and surfaces to the caller instead of half-applying.
    func set(_ url: String) throws {
        let next = Self.normalizeBaseUrl(url)
        if next.isEmpty {
            storage.delete(Self.storageKey)
        } else {
            try storage.write(Self.storageKey, next)
        }
        baseUrl = next
        onChange?(next)
    }
}
