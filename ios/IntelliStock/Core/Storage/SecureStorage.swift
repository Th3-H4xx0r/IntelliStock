import Foundation

/// The key-value secret store the session, server URL and lock settings
/// persist through — `FlutterSecureStorage` in the Flutter app.
///
/// `KeychainStore` is the production implementation; `InMemorySecureStorage`
/// backs tests and previews.
nonisolated protocol SecureStorage: Sendable {
    func read(_ key: String) -> String?
    func write(_ key: String, _ value: String) throws
    func delete(_ key: String)
}

extension KeychainStore: SecureStorage {}

/// A dictionary-backed `SecureStorage` for tests and previews. Thread-safe.
nonisolated final class InMemorySecureStorage: SecureStorage, @unchecked Sendable {
    private let lock = NSLock()
    private var values: [String: String]
    /// When set, every `write` throws it (to test best-effort persistence).
    var writeError: (any Error)? {
        get { lock.withLock { _writeError } }
        set { lock.withLock { _writeError = newValue } }
    }
    private var _writeError: (any Error)?

    init(_ initial: [String: String] = [:]) {
        values = initial
    }

    func read(_ key: String) -> String? {
        lock.withLock { values[key] }
    }

    func write(_ key: String, _ value: String) throws {
        try lock.withLock {
            if let _writeError { throw _writeError }
            values[key] = value
        }
    }

    func delete(_ key: String) {
        lock.withLock { _ = values.removeValue(forKey: key) }
    }

    /// Everything stored, for assertions.
    var snapshot: [String: String] { lock.withLock { values } }
}
