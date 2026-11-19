import Foundation
import Testing
@testable import IntelliStock

/// A tapped MCP_APPROVAL push opens that approval: the id is read off the
/// payload, held until the app attaches, and routed through the deep-link gate.
struct ApprovalPushTests {
    @Test func findsTheApprovalIdOnlyOnApprovalPushes() {
        let aps: [String: Any] = ["alert": ["title": "Jarvis wants to act", "body": "BUY 10 TQQQ on alpaca-main — LIVE"]]
        #expect(ApprovalPush.approvalId(from: ["aps": aps, "category": "MCP_APPROVAL", "approval_id": "ap_7f3c"]) == "ap_7f3c")
        #expect(ApprovalPush.approvalId(from: ["aps": ["category": "MCP_APPROVAL"], "approval_id": "ap_7f3c"]) == "ap_7f3c")
        #expect(ApprovalPush.approvalId(from: ["category": "app_update", "approval_id": "ap_7f3c"]) == nil)
        #expect(ApprovalPush.approvalId(from: ["category": "MCP_APPROVAL"]) == nil)
        #expect(ApprovalPush.approvalId(from: ["category": "MCP_APPROVAL", "approval_id": "../brokerages"]) == nil)
        #expect(ApprovalPush.approvalId(from: ["category": "MCP_APPROVAL", "approval_id": 42]) == nil)
        #expect(ApprovalPush.approvalId(from: ["category": "MCP_APPROVAL", "approval_id": String(repeating: "a", count: 500)]) == nil)
    }

    @Test func aTapBeforeAttachIsHeldThenOpened() {
        let delegate = AppDelegate()
        var opened: [String] = []
        delegate.routeTap(isUpdate: false, approvalId: "ap_1")
        delegate.attach(openApproval: { opened.append($0) })
        #expect(opened == ["ap_1"])
        delegate.routeTap(isUpdate: false, approvalId: "ap_2")
        delegate.routeTap(isUpdate: true, approvalId: nil)            // an update push never opens an approval
        delegate.routeTap(isUpdate: false, approvalId: nil)
        #expect(opened == ["ap_1", "ap_2"])
    }

    @Test func openApprovalPushesTheDetailWhenSignedInAndWaitsWhenSignedOut() {
        let signedIn = [ApiBaseUrlStore.storageKey: "https://api.example.test", SessionStore.tokenKey: "jwt",
                        SessionStore.userKey: #"{"username": "pk", "has_completed_onboarding": true}"#]
        let services = AppServices(storage: InMemorySecureStorage(signedIn), biometrics: FakeBiometrics(available: true, authResult: true),
                                   widgetSync: WidgetSync(defaults: nil), pushRegistrar: NoopPushRegistrar())
        services.openApproval("ap_7f3c")
        #expect(services.router.stack(for: services.router.tab).last == .approval("ap_7f3c"))
        services.openApproval("../x")
        #expect(services.router.stack(for: services.router.tab).count == 1)

        let signedOut = AppServices(storage: InMemorySecureStorage([ApiBaseUrlStore.storageKey: "https://api.example.test"]),
                                    widgetSync: WidgetSync(defaults: nil), pushRegistrar: NoopPushRegistrar())
        signedOut.openApproval("ap_7f3c")
        #expect(signedOut.loginRedirect == "/approvals/ap_7f3c")
    }
}
