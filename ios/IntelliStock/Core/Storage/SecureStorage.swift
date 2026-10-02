import Foundation
import Security

/// The key-value secret store the session, server URL and lock settings
/// persist through — `FlutterSecureStorage` in the Flutter app.
///
/// `KeychainStore` is the production implementation; `InMemorySecureStorage`
/// backs tests and previews.
nonisolated protocol SecureStorage: Sendable {
    /// The value, or nil for "not stored" and for any failure.
    func read(_ key: String) -> String?
    /// The value, nil only when nothing is stored; throws when the store is
    /// unavailable (e.g. the keychain before first unlock).
    func readChecked(_ key: String) throws -> String?
    func write(_ key: String, _ value: String) throws
    func delete(_ key: String)
}

nonisolated extension SecureStorage {
    /// Stores that cannot fail to read answer `read`.
    func readChecked(_ key: String) throws -> String? { read(key) }
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
    /// When set, every `readChecked` throws it and `read` returns nil — a
    /// keychain that is not available yet.
    var readError: (any Error)? {
        get { lock.withLock { _readError } }
        set { lock.withLock { _readError = newValue } }
    }
    private var _readError: (any Error)?

    init(_ initial: [String: String] = [:]) {
        values = initial
    }

    func read(_ key: String) -> String? {
        (try? readChecked(key)) ?? nil
    }

    func readChecked(_ key: String) throws -> String? {
        try lock.withLock {
            if let _readError { throw _readError }
            return values[key]
        }
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

nonisolated extension KeychainError {
    /// The keychain is locked (before first unlock after a reboot).
    static let interactionNotAllowed = KeychainError(status: errSecInteractionNotAllowed)
}
