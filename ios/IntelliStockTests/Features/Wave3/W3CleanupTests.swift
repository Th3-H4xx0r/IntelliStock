import Foundation
import Testing
@testable import IntelliStock

// Wave 3 dedupe and cleanup.

@Suite struct W3SymbolAdditionsTests {
    @Test func agentRunStatusGlyphsResolveThroughTheMap() {
        #expect(Symbol.named("do_not_disturb_on") == "minus.circle")
        #expect(Symbol.named("radio_button_unchecked") == "circle")
        #expect(Symbol.materialNames.contains("do_not_disturb_on"))
        #expect(Symbol.materialNames.contains("radio_button_unchecked"))
    }
}

/// The core cancellation checks that replaced the feature copies.
@Suite struct W3CoreCancellationTests {
    @Test func cancellationErrorsAreRecognised() {
        #expect(CancellationError().isCancellation)
        #expect(URLError(.cancelled).isCancellation)
        #expect(!URLError(.timedOut).isCancellation)
        #expect(!ApiError(message: "x").isCancellation)
    }

    @Test func aCancelledTaskTurnsAnyErrorIntoACancellation() async {
        let plain = ApiError(message: "late")
        #expect(!plain.isCancellationOrTaskCancelled)
        let task = Task { () -> Bool in
            withUnsafeCurrentTask { $0?.cancel() }
            return plain.isCancellationOrTaskCancelled
        }
        #expect(await task.value)
    }
}

/// The chatbot model now lives in AppServices, reset per session.
@MainActor
@Suite struct W3ChatbotServicesTests {
    @Test func theSessionKeepsOneChatbotUntilSignOut() {
        let services = AppServices(
            storage: InMemorySecureStorage([
                ApiBaseUrlStore.storageKey: "https://chat.example.test",
                SessionStore.tokenKey: "jwt",
                SessionStore.userKey: #"{"has_completed_onboarding": true}"#,
            ]),
            biometrics: FakeBiometrics(available: false, authResult: false),
            widgetSync: WidgetSyncProbe().sync,
            urlSession: DataStubProtocol.session,
            pushRegistrar: FakePushRegistrar(grant: false)
        )
        let first = services.chatbot
        #expect(services.chatbot === first)
        first.open()
        services.didSignOut()
        #expect(services.chatbot !== first)
        #expect(!services.chatbot.state.isOpen)
    }
}
