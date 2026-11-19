import CryptoKit
import Foundation
import Security
import Testing
@testable import IntelliStock

/// The approval signers. The server vector pins the algorithm and both
/// encodings to what the API verifies with; no test touches the Secure
/// Enclave or shows a Face ID prompt.
struct ApprovalSignerTests {
    // Produced by Python's `cryptography` (ec.ECDSA(hashes.SHA256()), DER
    // signature, X9.62 uncompressed point), the library the API verifies with.
    static let serverPub = Data(base64Encoded: "BPr89QgQ9Ywp13lwYgOI0R9yI5AQggFqAh/0hDhGoqOuyhBMVLcMfHdUbK9OXP+OZk5Z0gntyHv1t6eZnKsrxVU=")!
    static let serverSig = Data(base64Encoded: "MEUCIEL1xTOHf4SMVIEI3flff9m8xDXxawhFsT5f/67F1m/QAiEArrJQXBsq3U7iVHhFQ7oELk0uKlGaPfSjwtwFjhwYDRk=")!
    static let serverMsg = Data("ap_7f3c|15d67143989b33dd3b4423b0f683824b4394e9af80191b336c6d79c15cec3d5d|approve|1763538000".utf8)

    @Test func aPythonSignatureVerifiesWithTheSigningAlgorithm() {
        #expect(ApprovalSigning.verify(Self.serverSig, Self.serverMsg, publicKey: Self.serverPub))
        let tampered = Data(String(decoding: Self.serverMsg, as: UTF8.self).replacingOccurrences(of: "approve", with: "deny").utf8)
        #expect(!ApprovalSigning.verify(Self.serverSig, tampered, publicKey: Self.serverPub))
    }

    @Test func softwareSignaturesVerifyWithCryptoKitAgainstTheExportedKey() async throws {
        let signer = SoftwareApprovalSigner(storage: InMemorySecureStorage())
        let pub = try signer.createKey()
        #expect(pub.count == 65 && pub.first == 0x04 && ApprovalSigning.isUncompressedP256(pub))
        let msg = ApprovalMessage.bytes(id: "ap_7f3c", bodySha256: kVectorAHash, decision: .approve, ts: 1_795_068_030)
        let der = try await signer.authorize(reason: "test").sign(msg)
        #expect(der.first == 0x30)                                           // DER SEQUENCE
        let key = try P256.Signing.PublicKey(x963Representation: pub)
        let sig = try P256.Signing.ECDSASignature(derRepresentation: der)
        #expect(key.isValidSignature(sig, for: msg))
        #expect(!key.isValidSignature(sig, for: Data("other".utf8)))
        #expect(ApprovalSigning.verify(der, msg, publicKey: pub))
    }

    @Test func theSoftwareKeySurvivesANewSignerAndCanBeDeleted() async throws {
        let storage = InMemorySecureStorage()
        let pub = try SoftwareApprovalSigner(storage: storage).createKey()
        let again = SoftwareApprovalSigner(storage: storage)
        #expect(again.publicKey() == pub)
        again.deleteKey()
        #expect(again.publicKey() == nil)
        #expect(storage.snapshot.isEmpty)
        await #expect(throws: ApprovalSignerError.noKey) { _ = try await again.authorize(reason: "test") }
    }

    @Test func secureEnclaveAttributesPinTheContract() throws {
        #expect(ApprovalSigning.accessFlags == [.privateKeyUsage, .biometryCurrentSet])
        #expect(ApprovalSigning.algorithm == .ecdsaSignatureMessageX962SHA256)
        let attrs = SecureEnclaveApprovalSigner.keyAttributes(tag: "t", access: try SecureEnclaveApprovalSigner.makeAccessControl())
        #expect(attrs[kSecAttrTokenID] as? String == kSecAttrTokenIDSecureEnclave as String)
        #expect(attrs[kSecAttrKeyType] as? String == kSecAttrKeyTypeECSECPrimeRandom as String)
        #expect(attrs[kSecAttrKeySizeInBits] as? Int == 256)
        let priv = try #require(attrs[kSecPrivateKeyAttrs] as? [CFString: Any])
        #expect(priv[kSecAttrIsPermanent] as? Bool == true)
        #expect(priv[kSecAttrApplicationTag] as? Data == Data("t".utf8))
        #expect(priv[kSecAttrAccessControl] != nil)
    }

    #if targetEnvironment(simulator)
    @Test func theSecureEnclaveSignerNeverFallsBackToSoftware() {
        #expect(throws: ApprovalSignerError.unavailable) { _ = try SecureEnclaveApprovalSigner(tag: "test.\(UUID().uuidString)").createKey() }
    }

    @Test func theSimulatorDefaultIsTheSoftwareSigner() {
        #expect(!ApprovalSigners.platformDefault(storage: InMemorySecureStorage()).isHardwareBacked)
    }
    #endif
}
