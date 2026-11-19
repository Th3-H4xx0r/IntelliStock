import Foundation
import LocalAuthentication
import Security

/// The cryptography a decision is signed with (contract §5): ECDSA P-256 over
/// SHA-256 with a DER signature, verified by the API against the device's
/// X9.62 uncompressed public point.
nonisolated enum ApprovalSigning {
    static let algorithm: SecKeyAlgorithm = .ecdsaSignatureMessageX962SHA256
    /// Every signature needs the current Face ID enrolment; a change in it
    /// invalidates the key for good.
    static let accessFlags: SecAccessControlCreateFlags = [.privateKeyUsage, .biometryCurrentSet]
    static let keyTag = "dev.pkrishna.intellistock.mcp-approval.v1"
    /// Signed once after registration to prove the new key works.
    static let selfTestMessage = Data("intellistock-approval-selftest".utf8)

    /// 65 bytes, `0x04 ‖ X ‖ Y`: the only public key form the API accepts.
    static func isUncompressedP256(_ key: Data) -> Bool {
        key.count == 65 && key.first == 0x04
    }

    /// The DER `signature` over `message` verifies against `publicKey`.
    /// A malformed key reads as false.
    static func verify(_ signature: Data, _ message: Data, publicKey: Data) -> Bool {
        guard isUncompressedP256(publicKey) else { return false }
        let attributes: [CFString: Any] = [
            kSecAttrKeyType: kSecAttrKeyTypeECSECPrimeRandom,
            kSecAttrKeyClass: kSecAttrKeyClassPublic,
        ]
        guard let key = SecKeyCreateWithData(publicKey as CFData, attributes as CFDictionary, nil) else { return false }
        return SecKeyVerifySignature(key, algorithm, message as CFData, signature as CFData, nil)
    }
}

nonisolated enum ApprovalSignerError: Error, Equatable, Sendable {
    /// This device has no Secure Enclave.
    case unavailable
    /// No key has been created on this iPhone.
    case noKey
    /// Face ID was dismissed.
    case cancelled
    /// The Face ID enrolment changed; the key can never sign again.
    case keyInvalidated
    case failed(String)
}

/// One authorisation's signing capability. Never log what it returns.
nonisolated protocol ApprovalSigningGrant: Sendable {
    /// The DER ECDSA signature over `message`.
    func sign(_ message: Data) throws -> Data
}

/// The approval key. Device builds sign only in the Secure Enclave.
nonisolated protocol ApprovalSigner: Sendable {
    var isHardwareBacked: Bool { get }
    /// The 65-byte X9.62 point; nil when there is no key.
    func publicKey() -> Data?
    /// Replaces any key; returns the new public key.
    func createKey() throws -> Data
    func deleteKey()
    /// Face ID (Secure Enclave) showing the reason, then a grant that signs without asking again.
    func authorize(reason: String) async throws -> any ApprovalSigningGrant
}

nonisolated enum ApprovalSigners {
    /// The only place a signer is chosen: the software key exists on the
    /// simulator alone, so a device build never signs outside the Secure Enclave.
    static func platformDefault(storage: any SecureStorage) -> any ApprovalSigner {
        #if targetEnvironment(simulator)
        return SoftwareApprovalSigner(storage: storage)
        #else
        return SecureEnclaveApprovalSigner()
        #endif
    }
}

/// Signs with one `SecKey`. `context` keeps a Secure Enclave key's Face ID
/// evaluation alive for the grant's lifetime.
nonisolated final class SecKeySigningGrant: ApprovalSigningGrant, @unchecked Sendable {
    private let key: SecKey
    private let context: LAContext?

    init(key: SecKey, context: LAContext? = nil) {
        self.key = key
        self.context = context
    }

    func sign(_ message: Data) throws -> Data {
        var error: Unmanaged<CFError>?
        if let signature = SecKeyCreateSignature(key, ApprovalSigning.algorithm, message as CFData, &error) {
            return signature as Data
        }
        let code = error.map { CFErrorGetCode($0.takeRetainedValue()) } ?? Int(errSecInternalError)
        switch OSStatus(truncatingIfNeeded: code) {
        case errSecItemNotFound, errSecAuthFailed, errSecInvalidKeyRef:
            throw ApprovalSignerError.keyInvalidated
        default:
            throw ApprovalSignerError.failed("OSStatus \(code)")
        }
    }
}
