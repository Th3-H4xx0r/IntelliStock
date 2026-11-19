import CryptoKit
import Foundation
import LocalAuthentication
import Security

/// The approval key in the Secure Enclave: P-256, usable only after Face ID
/// with the current enrolment, never exported. Compiled for every
/// destination; where there is no Secure Enclave it throws `.unavailable`
/// and never falls back to software.
nonisolated final class SecureEnclaveApprovalSigner: ApprovalSigner {
    let tag: String

    init(tag: String = ApprovalSigning.keyTag) {
        self.tag = tag
    }

    var isHardwareBacked: Bool { true }

    static func makeAccessControl() throws -> SecAccessControl {
        var error: Unmanaged<CFError>?
        guard let access = SecAccessControlCreateWithFlags(
            kCFAllocatorDefault, kSecAttrAccessibleWhenUnlockedThisDeviceOnly, ApprovalSigning.accessFlags, &error
        ) else {
            let code = error.map { CFErrorGetCode($0.takeRetainedValue()) } ?? Int(errSecInternalError)
            throw ApprovalSignerError.failed("OSStatus \(code)")
        }
        return access
    }

    static func keyAttributes(tag: String, access: SecAccessControl) -> [CFString: Any] {
        [
            kSecAttrKeyType: kSecAttrKeyTypeECSECPrimeRandom,
            kSecAttrKeySizeInBits: 256,
            kSecAttrTokenID: kSecAttrTokenIDSecureEnclave,
            kSecPrivateKeyAttrs: [
                kSecAttrIsPermanent: true,
                kSecAttrApplicationTag: Data(tag.utf8),
                kSecAttrAccessControl: access,
            ] as [CFString: Any],
        ]
    }

    func publicKey() -> Data? {
        // Reading the public half never prompts.
        let context = LAContext()
        context.interactionNotAllowed = true
        guard let (key, _) = try? lookup(context: context), let pub = SecKeyCopyPublicKey(key) else { return nil }
        guard let data = SecKeyCopyExternalRepresentation(pub, nil) as Data?,
              ApprovalSigning.isUncompressedP256(data) else { return nil }
        return data
    }

    func createKey() throws -> Data {
        guard Self.secureEnclaveAvailable else { throw ApprovalSignerError.unavailable }
        let attributes = Self.keyAttributes(tag: tag, access: try Self.makeAccessControl())
        deleteKey()
        var error: Unmanaged<CFError>?
        guard let key = SecKeyCreateRandomKey(attributes as CFDictionary, &error) else {
            let code = error.map { CFErrorGetCode($0.takeRetainedValue()) } ?? Int(errSecInternalError)
            throw ApprovalSignerError.failed("OSStatus \(code)")
        }
        guard let pub = SecKeyCopyPublicKey(key),
              let data = SecKeyCopyExternalRepresentation(pub, nil) as Data?,
              ApprovalSigning.isUncompressedP256(data) else {
            deleteKey()
            throw ApprovalSignerError.failed("public key export")
        }
        return data
    }

    func deleteKey() {
        let query: [CFString: Any] = [
            kSecClass: kSecClassKey,
            kSecAttrApplicationTag: Data(tag.utf8),
            kSecAttrKeyClass: kSecAttrKeyClassPrivate,
        ]
        SecItemDelete(query as CFDictionary)
    }

    func authorize(reason: String) async throws -> any ApprovalSigningGrant {
        guard let (_, stored) = try lookup(context: nil) else { throw ApprovalSignerError.noKey }
        // The key's own access control; one built with the same flags if the
        // keychain did not return it.
        let access = try stored ?? Self.makeAccessControl()

        let context = LAContext()
        do {
            guard try await context.evaluateAccessControl(access, operation: .useKeySign, localizedReason: reason) else {
                throw ApprovalSignerError.failed("not authorised")
            }
        } catch let error as ApprovalSignerError {
            throw error
        } catch let error as LAError {
            switch error.code {
            case .userCancel, .appCancel, .systemCancel, .userFallback:
                throw ApprovalSignerError.cancelled
            default:
                throw ApprovalSignerError.failed(error.localizedDescription)
            }
        } catch {
            throw ApprovalSignerError.failed(error.localizedDescription)
        }

        guard let (key, _) = try lookup(context: context) else { throw ApprovalSignerError.noKey }
        return SecKeySigningGrant(key: key, context: context)
    }

    /// CryptoKit reports a Secure Enclave on the Apple-silicon simulator, but
    /// creating a key there fails ("not supported on iOS Simulator"), so the
    /// simulator counts as having none.
    private static var secureEnclaveAvailable: Bool {
        #if targetEnvironment(simulator)
        return false
        #else
        return SecureEnclave.isAvailable
        #endif
    }

    /// The private key reference and its own access control, or nil when
    /// there is no key under `tag`. Any other keychain status throws, so a
    /// locked or failing keychain never reads as "register again".
    private func lookup(context: LAContext?) throws -> (SecKey, SecAccessControl?)? {
        var query: [CFString: Any] = [
            kSecClass: kSecClassKey,
            kSecAttrApplicationTag: Data(tag.utf8),
            kSecAttrKeyClass: kSecAttrKeyClassPrivate,
            kSecReturnRef: true,
            kSecReturnAttributes: true,
        ]
        if let context { query[kSecUseAuthenticationContext] = context }
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw ApprovalSignerError.failed("OSStatus \(status)") }
        guard let attributes = result as? [CFString: Any],
              let ref = attributes[kSecValueRef] as CFTypeRef?,
              CFGetTypeID(ref) == SecKeyGetTypeID() else { return nil }
        let key = ref as! SecKey
        var access: SecAccessControl?
        if let value = attributes[kSecAttrAccessControl] as CFTypeRef?,
           CFGetTypeID(value) == SecAccessControlGetTypeID() {
            access = (value as! SecAccessControl)
        }
        return (key, access)
    }
}
