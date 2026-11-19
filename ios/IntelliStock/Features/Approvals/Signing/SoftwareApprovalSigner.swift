import Foundation
import Security

/// A software P-256 key for the simulator, which has no Secure Enclave. Only
/// `ApprovalSigners.platformDefault` on the simulator, and tests, construct
/// it; it never prompts.
nonisolated final class SoftwareApprovalSigner: ApprovalSigner {
    let storage: any SecureStorage
    let storageKey: String

    init(storage: any SecureStorage, storageKey: String = "mcp_approval_software_key") {
        self.storage = storage
        self.storageKey = storageKey
    }

    var isHardwareBacked: Bool { false }

    func publicKey() -> Data? {
        guard let key = privateKey(), let pub = SecKeyCopyPublicKey(key),
              let data = SecKeyCopyExternalRepresentation(pub, nil) as Data?,
              ApprovalSigning.isUncompressedP256(data) else { return nil }
        return data
    }

    func createKey() throws -> Data {
        let attributes: [CFString: Any] = [
            kSecAttrKeyType: kSecAttrKeyTypeECSECPrimeRandom,
            kSecAttrKeySizeInBits: 256,
        ]
        var error: Unmanaged<CFError>?
        guard let key = SecKeyCreateRandomKey(attributes as CFDictionary, &error) else {
            let code = error.map { CFErrorGetCode($0.takeRetainedValue()) } ?? Int(errSecInternalError)
            throw ApprovalSignerError.failed("OSStatus \(code)")
        }
        // 97 bytes: 0x04 ‖ X ‖ Y ‖ D.
        guard let raw = SecKeyCopyExternalRepresentation(key, nil) as Data?,
              let pub = SecKeyCopyPublicKey(key),
              let data = SecKeyCopyExternalRepresentation(pub, nil) as Data?,
              ApprovalSigning.isUncompressedP256(data) else {
            throw ApprovalSignerError.failed("key export")
        }
        do {
            try storage.write(storageKey, raw.base64EncodedString())
        } catch {
            throw ApprovalSignerError.failed("keychain write")
        }
        return data
    }

    func deleteKey() {
        storage.delete(storageKey)
    }

    func authorize(reason: String) async throws -> any ApprovalSigningGrant {
        guard let key = privateKey() else { throw ApprovalSignerError.noKey }
        return SecKeySigningGrant(key: key)
    }

    private func privateKey() -> SecKey? {
        guard let stored = storage.read(storageKey), let raw = Data(base64Encoded: stored) else { return nil }
        let attributes: [CFString: Any] = [
            kSecAttrKeyType: kSecAttrKeyTypeECSECPrimeRandom,
            kSecAttrKeyClass: kSecAttrKeyClassPrivate,
            kSecAttrKeySizeInBits: 256,
        ]
        return SecKeyCreateWithData(raw as CFData, attributes as CFDictionary, nil)
    }
}
