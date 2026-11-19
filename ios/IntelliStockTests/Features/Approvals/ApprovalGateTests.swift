import Foundation
import Testing
@testable import IntelliStock

/// Who may decide what, checked in the order the plan fixes: status, expiry,
/// registration, activation, then the fingerprint (which blocks Approve only).
struct ApprovalGateTests {
    @Test func gateRulesInPrecedenceOrder() {
        let a = approvalA()
        #expect(approvalGate(a, now: kT0, device: kActiveDevice, fingerprintMatches: true) == .open)
        #expect(approvalGate(approvalA(status: "executed"), now: kT0, device: kActiveDevice, fingerprintMatches: true)
                == .closed("This request is done — there's nothing to decide."))
        #expect(approvalGate(a, now: kT0 + 900, device: kActiveDevice, fingerprintMatches: true) == .closed(kApprovalExpired))
        #expect(approvalGate(a, now: kT0, device: nil, fingerprintMatches: true) == .closed(kApprovalRegisterFirst))
        let soon = ApprovalDeviceRecord(baseURL: "https://api.test", deviceId: "dev_1", name: "iPhone", activeAt: kT0 + 570)
        #expect(approvalGate(a, now: kT0, device: soon, fingerprintMatches: true) == .closed("This iPhone becomes an approver in 10 min."))
        #expect(approvalGate(a, now: kT0 + 550, device: soon, fingerprintMatches: true) == .closed("This iPhone becomes an approver in 1 min."))
        #expect(approvalGate(a, now: kT0, device: kActiveDevice, fingerprintMatches: false) == .denyOnly(kApprovalFingerprintMismatch))
        #expect(ApprovalGate.denyOnly("x").canDeny && !ApprovalGate.denyOnly("x").canApprove)
    }

    @Test func eachGateSaysWhatItAllowsAndWhy() {
        #expect(ApprovalGate.open.canApprove && ApprovalGate.open.canDeny && ApprovalGate.open.reason == nil)
        #expect(ApprovalGate.denyOnly("x").reason == "x")
        #expect(!ApprovalGate.closed("y").canApprove && !ApprovalGate.closed("y").canDeny)
        #expect(ApprovalGate.closed("y").reason == "y")
    }

    @Test func statusWordsReadAsPlainEnglish() {
        #expect(approvalStatusWord("approved") == "approved")
        #expect(approvalStatusWord("executed") == "done")
        #expect(approvalStatusWord("failed") == "failed")
        #expect(approvalStatusWord("denied") == "denied")
        #expect(approvalStatusWord("expired") == "expired")
        #expect(approvalStatusWord("queued") == "queued")
    }
}
