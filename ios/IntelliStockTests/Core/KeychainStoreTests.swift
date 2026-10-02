import Foundation
import Security
import Testing
@testable import IntelliStock

/// The native app must read what the Flutter build's flutter_secure_storage
/// wrote: generic passwords under service `flutter_secure_storage_service`,
/// account = key, UTF-8 data. These tests use a throwaway service name except
/// where they prove that exact layout.
@Suite(.serialized)
struct KeychainStoreTests {
    private let store = KeychainStore(service: "intellistock.tests.\(UUID().uuidString)")

    @Test func writeReadDelete() throws {
        try store.write("k", "v1")
        #expect(store.read("k") == "v1")
        try store.write("k", "v2")
        #expect(store.read("k") == "v2")
        store.delete("k")
        #expect(store.read("k") == nil)
    }

    @Test func missingKeyReadsNil() {
        #expect(store.read("never-written") == nil)
    }

    @Test func readsAnItemWrittenTheFlutterWay() throws {
        let key = "flutter-compat-\(UUID().uuidString)"
        let add: [CFString: Any] = [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: KeychainStore.flutterService,
            kSecAttrAccount: key,
            kSecAttrAccessible: kSecAttrAccessibleWhenUnlocked,
            kSecAttrSynchronizable: false,
            kSecValueData: Data("https://api.example.test".utf8),
        ]
        #expect(SecItemAdd(add as CFDictionary, nil) == errSecSuccess)
        defer { KeychainStore().delete(key) }

        #expect(KeychainStore().read(key) == "https://api.example.test")
    }
}
