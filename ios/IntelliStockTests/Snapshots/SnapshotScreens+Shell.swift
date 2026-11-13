import SwiftUI
import Testing
import UIKit
@testable import IntelliStock

/// The shell area's screens for the snapshot harness (see
/// SnapshotHarness.swift): live trading, the swing review and lanes, sign-in,
/// the lock, connect, onboarding, the chat and the More tab. Skipped unless
/// `IS_SNAPSHOT_DIR` is set. Names start `shell-`, so
/// `IS_SNAPSHOT_ONLY=shell` renders just these.
@Suite(.serialized, .enabled(if: SnapshotConfig.isEnabled))
@MainActor
struct SnapshotScreensShell {
    // MARK: More

    @Test func more() async throws {
        try await Snapshot.capture("shell-more") { services in
            SnapshotHost.shell(services, tab: .more)
        }
    }

    // MARK: Live trading

    @Test func liveTrading() async throws {
        try await Snapshot.capture("shell-live", pages: 6, settle: .seconds(6)) { services in
            SnapshotHost.shell(services, tab: .instances, stack: [.liveTrading(SnapshotFixtures.shellLiveId)])
        }
    }

    @Test func liveManualOrder() async throws {
        try await Snapshot.capture("shell-live-order") { services in
            SnapshotHost.sheet { LiveManualOrderSheet(model: Self.liveModel(services)) }
        }
    }

    @Test func liveHalt() async throws {
        try await Snapshot.capture("shell-live-halt") { services in
            SnapshotHost.sheet(detents: [.medium]) { LiveHaltSheet(model: Self.liveModel(services)) }
        }
    }

    private static func liveModel(_ services: AppServices) -> LiveTradingModel {
        LiveTradingModel(instanceId: SnapshotFixtures.shellLiveId, repository: { services.liveRepository })
    }

    // MARK: Swing

    /// The pending signals card and the wheel's sections, as the instance
    /// screen lists them.
    @Test func swingLanes() async throws {
        let env = Snapshot.makeServices()
        let services = env.services
        let signals = PendingSignalsModel(instanceId: SnapshotFixtures.shellSwingId, source: { services.swingRepository })
        let wheel = WheelModel(instanceId: SnapshotFixtures.shellSwingId, source: { services.swingRepository })
        await signals.build()
        await wheel.load()
        let actions = PendingSignalsActions()
        try await Snapshot.capture("shell-swing", environment: env, pages: 5) { _ in
            NavigationStack {
                List {
                    PendingSignalsSection(model: signals, actions: actions, cash: wheel.state.value?.cash, showToast: { _ in })
                    WheelSections(
                        model: wheel,
                        pendingIds: Set(signals.state.value?.signals.map(\.id) ?? []),
                        brokerageId: SnapshotFixtures.swingPaperId
                    )
                }
                .dsList()
                .navigationTitle("Swing lab")
                .navigationBarTitleDisplayMode(.inline)
            }
        }
    }

    /// The review before approving a sold put (collateral above the cash).
    /// The swing/wheel instance's own detail screen, where the lanes live in
    /// the app: checks the swing cards sit in the restyled list.
    @Test func instanceDetailSwing() async throws {
        try await Snapshot.capture("shell-instance-detail-swing", pages: 4, settle: .seconds(5)) { services in
            SnapshotHost.shell(services, tab: .instances, stack: [.instance(SnapshotFixtures.shellSwingId)])
        }
    }

    @Test func swingReviewWheel() async throws {
        let review = Self.review(Self.xle, decision: "approve")
        try await Snapshot.capture("shell-review-wheel", settle: .seconds(4)) { services in
            SnapshotHost.sheet { SwingOrderReviewSheet(review: review, cash: 8_400).environment(services) }
        }
    }

    /// The review before approving half a swing entry.
    @Test func swingReviewSwing() async throws {
        let review = Self.review(Self.amd, decision: "approve_half")
        try await Snapshot.capture("shell-review-swing", settle: .seconds(4)) { services in
            SnapshotHost.sheet { SwingOrderReviewSheet(review: review, cash: nil).environment(services) }
        }
    }

    private static func review(_ json: JSON, decision: String) -> SwingOrderReview {
        SwingOrderReview(
            signal: SwingSignal(json: json),
            decision: decision,
            send: { DecisionResult(.recorded, "Snapshot: nothing was sent.") },
            finished: { _ in }
        )
    }

    private static let xle: JSON = [
        "id": "sig-xle", "lane": "wheel", "symbol": "XLE", "session": "2026-11-12",
        "created_at": "2026-11-12T16:53:43Z", "score": 54, "recommendation": "REVIEW",
        "reasoning": "IV rank 41 with support at 88; one week out.", "key_risks": [],
        "proposal": ["contract": "XLE261120P00088000", "strike": 88, "expiry": "2026-11-20", "qty": 1,
                     "limit_price": 0.92, "premium_est": 0.95],
        "status": "pending",
    ]

    private static let amd: JSON = [
        "id": "sig-amd", "lane": "swing", "symbol": "AMD", "session": "2026-11-12",
        "created_at": "2026-11-12T17:23:43Z", "score": 68, "recommendation": "REVIEW",
        "reasoning": "Pullback to the rising 50-day.", "key_risks": ["Semis guide-down risk"],
        "proposal": ["entry": 148.20, "stop": 139.50, "target": 166.00, "shares": 12],
        "status": "pending",
    ]

    // MARK: Sign-in, lock, connect

    @Test func login() async throws {
        try await Snapshot.capture("shell-login", settle: .seconds(6)) { _ in
            LoginView(redirectPath: nil)
        }
    }

    @Test func lock() async throws {
        try await Snapshot.capture("shell-lock", settle: .seconds(3)) { _ in
            LockView()
        }
    }

    @Test func connect() async throws {
        try await Snapshot.capture("shell-connect") { _ in
            NavigationStack { ConnectView() }
        }
    }

    // MARK: Onboarding

    @Test func onboardingWelcome() async throws {
        try await Snapshot.capture("shell-onboarding-1-welcome") { _ in
            OnboardingView()
        }
    }

    @Test func onboardingAbout() async throws {
        try await Snapshot.capture("shell-onboarding-2-about") { _ in
            Self.step { OnboardingAboutStep() }
        }
    }

    @Test func onboardingModel() async throws {
        try await Snapshot.capture("shell-onboarding-3-model") { services in
            Self.step { OnboardingAddModelStep(model: Self.onboarding(services)) }
        }
    }

    @Test func onboardingBrokerage() async throws {
        try await Snapshot.capture("shell-onboarding-4-brokerage") { services in
            Self.step { OnboardingLinkBrokerageStep(model: Self.onboarding(services)) }
        }
    }

    @Test func onboardingInstance() async throws {
        try await Snapshot.capture("shell-onboarding-5-instance") { services in
            Self.step { OnboardingCreateInstanceStep(model: Self.onboarding(services)) }
        }
    }

    @Test func onboardingConnect() async throws {
        try await Snapshot.capture("shell-onboarding-6-connect", pages: 2) { _ in
            Self.step { OnboardingConnectStep() }
        }
    }

    @Test func onboardingComplete() async throws {
        let env = Snapshot.makeServices()
        let model = Self.onboarding(env.services)
        await model.loadState()
        try await Snapshot.capture("shell-onboarding-7-complete", environment: env) { _ in
            Self.step { OnboardingCompleteStep(model: model) }
        }
    }

    private static func onboarding(_ services: AppServices) -> OnboardingModel {
        OnboardingModel(repository: { services.onboardingRepository }, session: services.session)
    }

    /// A step on the page colour, as the onboarding screen shows it between
    /// its stepper and footer (those two are private to the screen).
    private static func step<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        content()
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(DS.Surface.page.ignoresSafeArea())
    }

    // MARK: Chat

    /// The chat sheet over the dashboard: a conversation with a stat, a
    /// table and a tool waiting for approval.
    @Test func chat() async throws {
        SnapshotFixtures.shellChat = .conversation
        defer { SnapshotFixtures.shellChat = .off }
        let env = Snapshot.makeServices()
        try await Snapshot.capture("shell-chat", environment: env, pages: 2, settle: .seconds(4), afterPrepare: .seconds(3)) { _ in
            env.services.chatbot.open()
        } content: { services in
            SnapshotHost.shell(services, tab: .dashboard)
        }
    }

    @Test func chatSettings() async throws {
        SnapshotFixtures.shellChat = .conversation
        defer { SnapshotFixtures.shellChat = .off }
        let env = Snapshot.makeServices()
        let model = env.services.chatbot
        await model.bootstrap()
        await model.loadModels()
        try await Snapshot.capture("shell-chat-settings", environment: env) { _ in
            SnapshotHost.sheet { ChatSettingsSheet(model: model) { _ in } }
        }
    }

    @Test func chatModelPicker() async throws {
        SnapshotFixtures.shellChat = .picker
        defer { SnapshotFixtures.shellChat = .off }
        let env = Snapshot.makeServices()
        try await Snapshot.capture("shell-chat-picker", environment: env, settle: .seconds(4), afterPrepare: .seconds(3)) { _ in
            env.services.chatbot.open()
        } content: { services in
            SnapshotHost.shell(services, tab: .dashboard)
        }
    }
}
