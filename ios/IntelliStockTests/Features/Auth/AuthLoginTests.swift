import Foundation
import Testing
@testable import IntelliStock

/// `test/features/auth/login_state_test.dart`.
@Suite struct AuthLoginStateTests {
    @Test func initialStateIsIdleWithNoError() {
        let s = LoginState()
        #expect(!s.isLoading)
        #expect(s.errorMessage == nil)
        #expect(!s.hasError)
    }

    @Test func copyWithIsLoadingSetsLoading() {
        let loading = LoginState().copyWith(isLoading: true)
        #expect(loading.isLoading)
        #expect(loading.errorMessage == nil)
    }

    @Test func copyWithErrorMessageSetsHasError() {
        let err = LoginState().copyWith(errorMessage: "Invalid credentials")
        #expect(err.hasError)
        #expect(err.errorMessage == "Invalid credentials")
    }

    @Test func copyWithClearsErrorMessageWhenNotSupplied() {
        let errState = LoginState().copyWith(errorMessage: "oops")
        let same = errState.copyWith(isLoading: false)
        #expect(same.errorMessage == nil)
    }

    @Test func clearedStateHasNoError() {
        let errState = LoginState().copyWith(errorMessage: "oops")
        let cleared = LoginState(isLoading: errState.isLoading)
        #expect(!cleared.hasError)
        #expect(cleared.errorMessage == nil)
    }

    @Test func descriptionIncludesFieldValues() {
        let s = LoginState().copyWith(isLoading: true, errorMessage: "e")
        #expect(s.description.contains("isLoading: true"))
        #expect(s.description.contains("errorMessage: e"))
        #expect(LoginState().description == "LoginState(isLoading: false, errorMessage: null, succeeded: false)")
    }

    @Test func copyWithKeepsSucceeded() {
        #expect(LoginState(succeeded: true).copyWith(isLoading: false).succeeded)
    }
}

/// `test/features/auth/login_entrance_test.dart`.
@MainActor
@Suite struct AuthLoginEntranceTests {
    @Test func revealsTheSignInControlsAfterTheCoinEntranceStarts() async {
        let clock = ManualClock()
        let entrance = LoginEntranceModel(sleep: clock.sleep)

        entrance.onCoinEntranceStarted()
        await clock.settle()
        #expect(!entrance.showForm)

        await clock.advance(by: .milliseconds(449))
        #expect(!entrance.showForm)

        await clock.advance(by: .milliseconds(1))
        #expect(entrance.showForm)
        entrance.dispose()
    }

    @Test func aSecondStartIsIgnored() async {
        let clock = ManualClock()
        let entrance = LoginEntranceModel(sleep: clock.sleep)
        entrance.onCoinEntranceStarted()
        await clock.advance(by: .milliseconds(300))
        entrance.onCoinEntranceStarted()
        await clock.advance(by: .milliseconds(150))
        #expect(entrance.showForm)
        #expect(clock.requested == [.milliseconds(450)])
    }

    @Test func disposeCancelsTheReveal() async {
        let clock = ManualClock()
        let entrance = LoginEntranceModel(sleep: clock.sleep)
        entrance.onCoinEntranceStarted()
        await clock.settle()
        entrance.dispose()
        await clock.advance(by: .seconds(1))
        #expect(!entrance.showForm)
    }
}

/// A coin renderer that records the clips it was told to play.
@MainActor
final class AuthCoinRecorder: LoginCoinPlaying {
    var ready: Bool
    private(set) var played: [CoinClip] = []
    private(set) var pauses = 0

    init(ready: Bool = true) {
        self.ready = ready
    }

    func play(_ clip: CoinClip) -> Bool {
        guard ready else { return false }
        played.append(clip)
        return true
    }

    func pause() { pauses += 1 }
}

/// `test/features/auth/login_coin_test.dart` plus the clip choreography of
/// `_LoginCoinState._play`.
@MainActor
@Suite struct AuthLoginCoinTests {
    private func make(ready: Bool = true) -> (LoginCoinModel, AuthCoinRecorder, ManualClock, Counter) {
        let clock = ManualClock()
        let player = AuthCoinRecorder(ready: ready)
        let model = LoginCoinModel(sleep: clock.sleep)
        let entrances = Counter()
        model.player = player
        model.onEntranceStarted = { entrances.value += 1 }
        return (model, player, clock, entrances)
    }

    final class Counter {
        var value = 0
    }

    @Test func startsTheAnimationWhenTheLoadCallbackIsMissed() async {
        let (model, player, clock, entrances) = make()
        model.start()
        await clock.advance(by: .milliseconds(1099))
        #expect(!model.started)

        await clock.advance(by: .milliseconds(1))
        #expect(model.started)          // the viewer's opacity goes to 1
        #expect(entrances.value == 1)
        #expect(player.played == [.intro])
        _ = player
    }

    @Test func loadPlaysTheIntroThenHandsOverToIdle() async {
        let (model, player, clock, entrances) = make()
        model.start()
        model.didLoad()
        #expect(model.started && model.loaded)
        #expect(player.played == [.intro])
        #expect(entrances.value == 1)

        await clock.advance(by: .milliseconds(2399))
        #expect(player.played == [.intro])
        await clock.advance(by: .milliseconds(1))
        #expect(player.played == [.intro, .idle])

        // The kick was cancelled by the load.
        await clock.advance(by: .seconds(5))
        #expect(player.played == [.intro, .idle])
    }

    @Test func workingSpinsAndARejectionSettlesStraightIntoIdle() async {
        let (model, player, clock, _) = make()
        model.didLoad()
        model.setPhase(.working)
        #expect(player.played == [.intro, .spin])

        // The intro's idle hand-over no longer applies once working.
        await clock.advance(by: .seconds(3))
        #expect(player.played == [.intro, .spin])

        model.setPhase(.idle)
        #expect(player.played == [.intro, .spin, .idle])
    }

    @Test func successPlaysTheTurnOver() {
        let (model, player, _, _) = make()
        model.didLoad()
        model.setPhase(.working)
        model.setPhase(.success)
        #expect(player.played.last == .success)
    }

    @Test func aRendererThatIsNotReadyKeepsTheIntroQueued() async {
        let (model, player, clock, entrances) = make(ready: false)
        model.start()
        await clock.advance(by: .milliseconds(1100))
        #expect(model.started)
        #expect(entrances.value == 1)
        #expect(player.played.isEmpty)

        player.ready = true
        model.didLoad()
        #expect(player.played == [.intro])
    }

    @Test func aFailedLoadShowsTheFallbackAndReleasesTheForm() async {
        let (model, player, clock, entrances) = make()
        model.start()
        model.didFail()
        #expect(model.failed)
        #expect(entrances.value == 1)
        await clock.advance(by: .seconds(2))
        #expect(player.played.isEmpty)
        #expect(entrances.value == 1)
    }

    @Test func backgroundPausesAndForegroundReplays() {
        let (model, player, _, _) = make()
        model.didEnterBackground()
        #expect(player.pauses == 0)    // nothing started yet
        model.didLoad()
        model.didEnterBackground()
        #expect(player.pauses == 1)
        model.didBecomeActive()
        #expect(player.played == [.intro, .idle])
    }

    @Test func samePhaseIsNotReplayed() {
        let (model, player, _, _) = make()
        model.didLoad()
        model.setPhase(.idle)
        #expect(player.played == [.intro])
    }

    @Test func clipFilesAndLooping() {
        #expect(CoinClip.allCases.map(\.file) == ["coin_intro", "coin_idle", "coin_spin", "coin_success"])
        #expect(CoinClip.idle.loops && CoinClip.spin.loops)
        #expect(!CoinClip.intro.loops && !CoinClip.success.loops)
    }

    @Test func everyClipIsBundled() {
        for clip in CoinClip.allCases {
            #expect(LoginCoinScene.url(for: clip) != nil, "missing \(clip.file).usdz")
        }
    }
}

/// `LoginController` and the screen's local state.
@MainActor
@Suite struct AuthLoginModelTests {
    private func makeSession(_ storage: InMemorySecureStorage = InMemorySecureStorage()) -> SessionStore {
        SessionStore(storage: storage, widgetSync: WidgetSync(defaults: nil))
    }

    @Test func successWritesTheSessionAfterTheHold() async throws {
        let stub = DataStub(json: #"{"access_token":"jwt-1","user":{"username":"pk","has_completed_onboarding":true}}"#)
        let client = stub.client
        let session = makeSession()
        let clock = ManualClock()
        let model = LoginModel(repository: { AuthRepository(client: client) }, session: session, sleep: clock.sleep)

        let task = Task { await model.login("pk", "pw", holdBeforeCommit: .milliseconds(1850)) }
        #expect(await eventually { model.state.succeeded })
        // Accepted, but the session is held back for the coin's flip.
        #expect(model.coinPhase == .success)
        #expect(!model.busy)
        #expect(session.token == nil)

        await clock.advance(by: .milliseconds(1850))
        #expect(await task.value)
        #expect(session.token == "jwt-1")
        #expect(session.username == "pk")
        #expect(session.hasCompletedOnboarding)

        let request = try #require(stub.last)
        #expect(request.method == "POST")
        #expect(request.path == "/auth/login")
        let body = try JSON(data: request.httpBody ?? Data())
        #expect(body == ["username": "pk", "password": "pw"])
    }

    @Test func aMissingTokenIsAnEmptyStringAndANonMapUserIsNil() async {
        let stub = DataStub(json: #"{"user":"nope"}"#)
        let client = stub.client
        let session = makeSession()
        let model = LoginModel(repository: { AuthRepository(client: client) }, session: session)
        #expect(await model.login("a", "b"))
        #expect(session.token == "")
        #expect(session.user == nil)
    }

    @Test func anApiErrorShowsItsMessage() async {
        let stub = DataStub(status: 401, json: #"{"detail":"Invalid username or password"}"#)
        let client = stub.client
        let model = LoginModel(repository: { AuthRepository(client: client) }, session: makeSession())
        #expect(await model.login("a", "b") == false)
        #expect(model.state.errorMessage == "Invalid username or password")
        #expect(!model.state.isLoading)
        #expect(model.coinPhase == .idle)

        model.clearError()
        #expect(!model.state.hasError)
    }

    @Test func aKeychainFailureBringsTheFormBackWithTheGenericMessage() async {
        let stub = DataStub(json: #"{"access_token":"jwt"}"#)
        let client = stub.client
        let storage = InMemorySecureStorage()
        storage.writeError = CocoaError(.fileWriteNoPermission)
        let model = LoginModel(repository: { AuthRepository(client: client) }, session: makeSession(storage))
        #expect(await model.login("a", "b") == false)
        #expect(model.state.errorMessage == "Something went wrong. Please try again.")
        #expect(!model.state.succeeded)
    }

    @Test func submitValidatesLocallyAndTrimsTheUsername() async throws {
        let stub = DataStub(json: #"{"access_token":"t"}"#)
        let client = stub.client
        let model = LoginModel(repository: { AuthRepository(client: client) }, session: makeSession(), sleep: { _ in })

        await model.submit(username: "   ", password: "pw")
        #expect(model.displayedError == "Please enter your username and password.")
        await model.submit(username: "pk", password: "")
        #expect(model.displayedError == "Please enter your username and password.")
        #expect(stub.requests.isEmpty)

        model.clearErrors()
        #expect(model.displayedError == nil)

        await model.submit(username: "  pk  ", password: " pw ")
        let body = try JSON(data: stub.last?.httpBody ?? Data())
        #expect(body == ["username": "pk", "password": " pw "])
    }

    @Test func biometricMethodNames() async {
        let model = LoginModel(repository: { AuthRepository(client: DataStub().client) }, session: makeSession())
        #expect(model.biometricAvailable == nil)

        await model.resolveBiometrics(FakeBiometrics(available: true, authResult: true, types: [.face]))
        #expect(model.biometricAvailable == true)
        #expect(model.biometricIsFace)
        #expect(model.biometricMethod == "Face ID")

        await model.resolveBiometrics(FakeBiometrics(available: true, authResult: true, types: [.fingerprint]))
        #expect(!model.biometricIsFace)
        #expect(model.biometricMethod == "Touch ID")

        await model.resolveBiometrics(FakeBiometrics(available: false, authResult: true, types: []))
        #expect(model.biometricAvailable == false)
        #expect(model.biometricMethod == "biometrics")
    }

    @Test func turningTheLockOnThatFailsReportsIt() async {
        let model = LoginModel(repository: { AuthRepository(client: DataStub().client) }, session: makeSession())
        let biometrics = FakeBiometrics(available: true, authResult: false, types: [.face])
        await model.resolveBiometrics(biometrics)
        let lock = AppLock(seed: AppLockState(), storage: InMemorySecureStorage(), biometrics: biometrics, isAuthenticated: { false })

        await model.toggleBiometricLock(true, lock: lock)
        #expect(model.localError == "Could not turn on Face ID.")
        #expect(!lock.enabled)
        #expect(!model.biometricBusy)

        biometrics.authResult = true
        await model.toggleBiometricLock(true, lock: lock)
        #expect(lock.enabled)
        await model.toggleBiometricLock(false, lock: lock)
        #expect(!lock.enabled)
    }
}
