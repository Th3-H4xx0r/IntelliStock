import Foundation
import SwiftUI
import Testing
import UIKit
@testable import IntelliStock

// Tests for the Wave 1 fix round 2 (N1 and the minors).

/// N1: a deferred keychain load must not strand the app on the blank screen.
@MainActor
struct DeferredKeychainTests {
    private let stub = DataStub()

    private func make(_ storage: InMemorySecureStorage, center: NotificationCenter = NotificationCenter()) -> AppServices {
        AppServices(
            storage: storage,
            biometrics: FakeBiometrics(available: true, authResult: true),
            widgetSync: WidgetSyncProbe().sync,
            urlSession: DataStubProtocol.session,
            pushRegistrar: FakePushRegistrar(grant: false),
            notificationCenter: center
        )
    }

    private var signedIn: [String: String] {
        [
            ApiBaseUrlStore.storageKey: stub.baseURL,
            SessionStore.tokenKey: "jwt",
            SessionStore.userKey: #"{"has_completed_onboarding": true}"#,
        ]
    }

    private func screen(_ services: AppServices) -> RootScreen {
        RootScreen.resolve(
            storageReady: services.isStorageReady,
            isConfigured: services.urlStore.isConfigured,
            isAuthenticated: services.session.isAuthenticated,
            hasCompletedOnboarding: services.session.hasCompletedOnboarding,
            locked: services.lock.locked,
            storageUnavailable: services.isStorageUnavailable
        )
    }

    /// Prewarmed while locked, the protected-data notification never
    /// arrives: becoming active (which implies unlocked) loads the session.
    @Test func becomingActiveLoadsTheDeferredSessionWithoutTheNotification() {
        let storage = InMemorySecureStorage(signedIn)
        storage.readError = KeychainError.interactionNotAllowed
        let services = make(storage)
        #expect(screen(services) == .waiting)

        storage.readError = nil
        services.scenePhaseChanged(.active)
        #expect(services.isStorageReady)
        #expect(services.session.isAuthenticated)
        #expect(services.apiClient.baseURL == stub.baseURL)
        #expect(screen(services) == .app(.main))
    }

    /// A keychain that still refuses in the foreground is a real error: the
    /// screen offers a retry instead of staying blank.
    @Test func aForegroundKeychainFailureOffersARetry() {
        let storage = InMemorySecureStorage(signedIn)
        storage.readError = KeychainError(status: -34018)
        let services = make(storage)
        services.scenePhaseChanged(.inactive)
        #expect(screen(services) == .waiting)
        services.scenePhaseChanged(.active)
        #expect(services.isStorageUnavailable)
        #expect(screen(services) == .storageUnavailable)
        #expect(!screen(services).showsAppContent)

        services.retryStorageLoad()
        #expect(screen(services) == .storageUnavailable)

        storage.readError = nil
        services.retryStorageLoad()
        #expect(!services.isStorageUnavailable)
        #expect(screen(services) == .app(.main))
    }

    @Test func theNotificationStillWorksAsTheBackgroundFastPath() {
        let storage = InMemorySecureStorage(signedIn)
        storage.readError = KeychainError.interactionNotAllowed
        let center = NotificationCenter()
        let services = make(storage, center: center)
        storage.readError = nil
        center.post(name: UIApplication.protectedDataDidBecomeAvailableNotification, object: nil)
        #expect(services.isStorageReady)
        #expect(!services.isStorageUnavailable)
    }

    @Test func activeIsANoOpOnceLoaded() {
        let services = make(InMemorySecureStorage(signedIn))
        #expect(services.isStorageReady)
        services.scenePhaseChanged(.active)
        #expect(!services.isStorageUnavailable)
        #expect(services.session.isAuthenticated)
    }
}

/// Minor 4: an unreadable lock preference fails safe.
struct AppLockSeedTests {
    @Test func anUnreadablePreferenceLocksASignedInSession() {
        let storage = InMemorySecureStorage()
        storage.readError = KeychainError(status: -34018)
        let signedIn = AppLock.seed(storage: storage, isAuthenticated: true)
        #expect(signedIn.enabled)
        #expect(signedIn.locked)
        let signedOut = AppLock.seed(storage: storage, isAuthenticated: false)
        #expect(!signedOut.locked)
    }

    @Test func aMissingPreferenceIsOff() {
        let seed = AppLock.seed(storage: InMemorySecureStorage(), isAuthenticated: true)
        #expect(!seed.enabled)
        #expect(!seed.locked)
        #expect(seed.timeout == .immediately)
    }
}

/// Minor 2: busy flags and the freshness stamp are generation-scoped.
@MainActor
struct DashboardGenerationTests {
    private let stub = DataStub(json: "{}")

    @Test func anOldRunDoesNotClearANewerRunsBusyFlag() async {
        let client = stub.client
        let model = DashboardModel(repository: { DashboardRepository(client: client) })

        var gateA: CheckedContinuation<Void, Never>?
        let runA = Task { await model.run("price_engine") { await withCheckedContinuation { gateA = $0 } } }
        #expect(await eventually { gateA != nil })
        model.reset()

        var gateB: CheckedContinuation<Void, Never>?
        let runB = Task { await model.run("price_engine") { await withCheckedContinuation { gateB = $0 } } }
        #expect(await eventually { gateB != nil })
        #expect(model.isBusy("price_engine"))

        gateA?.resume()
        await runA.value
        #expect(model.isBusy("price_engine"))

        gateB?.resume()
        await runB.value
        #expect(!model.isBusy("price_engine"))
    }

    @Test func aStaleFreshnessStampIsIgnored() {
        let model = DashboardModel(repository: { DashboardRepository(client: DataStub().client) })
        let started = model.currentGeneration
        model.reset()
        model.stampPortfolioUpdated(startedGeneration: started)
        #expect(model.portfolioUpdatedAt == nil)
        let now = Date()
        model.stampPortfolioUpdated(startedGeneration: model.currentGeneration, at: now)
        #expect(model.portfolioUpdatedAt == now)
    }
}

/// Minor 3: a cancelled token-usage load is not "6 of 6 requests failed".
@MainActor
struct TokenUsageCancellationTests {
    private let stub = DataStub(json: "{}")

    @Test func aCancelledLoadThrowsCancellation() async {
        let repo = TokenUsageRepository(client: stub.client)
        let task = Task { try await repo.fetchAllUnlessCancelled("7d") }
        task.cancel()
        guard case .failure(let error) = await task.result else {
            Issue.record("expected the cancelled load to throw")
            return
        }
        #expect(error is CancellationError)
    }

    @Test func theNonThrowingFormNeverReportsACancelAsFailures() async {
        let repo = TokenUsageRepository(client: stub.client)
        let task = Task { await repo.fetchAll("7d") }
        task.cancel()
        let data = await task.value
        #expect(data.partialError == nil)
    }

    @Test func realFailuresAreStillCounted() async throws {
        stub.respond(status: 500, json: #"{"detail": "down"}"#)
        let data = try await TokenUsageRepository(client: stub.client).fetchAllUnlessCancelled("7d")
        #expect(data.partialError == "6 of 6 requests failed: down")
    }
}
