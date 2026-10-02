import Foundation
import Observation

/// State for the login operation — idle / loading / error. `LoginState` in
/// `auth_controller.dart`.
nonisolated struct LoginState: Equatable, Sendable, CustomStringConvertible {
    var isLoading = false
    var errorMessage: String?
    /// Credentials accepted, but the session is not committed yet — the login
    /// screen is playing its exit before the gate takes over.
    var succeeded = false

    var hasError: Bool { errorMessage != nil }

    /// Dart's `copyWith`: an omitted `errorMessage` CLEARS it (the controller
    /// relies on that to drop a stale error).
    func copyWith(isLoading: Bool? = nil, errorMessage: String? = nil, succeeded: Bool? = nil) -> LoginState {
        LoginState(
            isLoading: isLoading ?? self.isLoading,
            errorMessage: errorMessage,
            succeeded: succeeded ?? self.succeeded
        )
    }

    var description: String {
        "LoginState(isLoading: \(isLoading), errorMessage: \(errorMessage ?? "null"), succeeded: \(succeeded))"
    }
}

/// The login flow — `LoginController` plus the screen-local state of
/// `_LoginScreenState` (local validation, the biometric row). The screen owns
/// it in `@State`, as the Dart provider was auto-disposed.
@Observable
final class LoginModel {
    /// How long the success sequence holds the screen before the gate takes
    /// over: the coin's `Success` clip (1.7 s) with a beat to breathe.
    static let successHold: Duration = .milliseconds(1850)

    private(set) var state = LoginState()
    /// `_localError`: validation and biometric-toggle failures.
    private(set) var localError: String?

    /// Nil until resolved; false hides the biometric row altogether.
    private(set) var biometricAvailable: Bool?
    private(set) var biometricMethod = "Face ID"
    private(set) var biometricIsFace = true
    private(set) var biometricBusy = false

    @ObservationIgnored private let repository: () -> AuthRepository
    @ObservationIgnored private let session: SessionStore
    @ObservationIgnored private let sleep: PollingSleep

    init(
        repository: @escaping () -> AuthRepository,
        session: SessionStore,
        sleep: @escaping PollingSleep = realPollingSleep
    ) {
        self.repository = repository
        self.session = session
        self.sleep = sleep
    }

    /// The error the banner shows: the controller's, else the local one.
    var displayedError: String? { state.errorMessage ?? localError }

    /// A request is in flight and not yet accepted.
    var busy: Bool { state.isLoading && !state.succeeded }

    var coinPhase: CoinPhase {
        state.succeeded ? .success : (busy ? .working : .idle)
    }

    // MARK: LoginController

    /// Attempts login. On success the session is written after
    /// `holdBeforeCommit`, so the screen can play its success animation; the
    /// gate follows the session. Returns `true` on success.
    @discardableResult
    func login(_ username: String, _ password: String, holdBeforeCommit: Duration = .zero) async -> Bool {
        state = state.copyWith(isLoading: true, errorMessage: nil)
        do {
            let data = try await repository().login(username, password)
            let token = data["access_token"]?.string ?? ""
            let user: JSON? = (data["user"]?.isObject ?? false) ? data["user"] : nil

            // Tell the screen it won before committing, so the coin can turn
            // over while the commit is held.
            state = LoginState(succeeded: true)
            if holdBeforeCommit > .zero {
                try? await sleep(holdBeforeCommit)
            }
            do {
                try await session.setSession(token: token, user: user)
            } catch {
                // The keychain refused the session. Dart's copyWith kept
                // `succeeded`, which left the form collapsed and the error
                // unseen; bring the form back so the message shows.
                state = LoginState(errorMessage: "Something went wrong. Please try again.")
                return false
            }
            return true
        } catch is CancellationError {
            // Not a failure to report: just stop the spinner.
            state = state.copyWith(isLoading: false, errorMessage: state.errorMessage)
            return false
        } catch let error as ApiError {
            state = state.copyWith(isLoading: false, errorMessage: error.message)
            return false
        } catch {
            state = state.copyWith(isLoading: false, errorMessage: "Something went wrong. Please try again.")
            return false
        }
    }

    /// Clears any displayed error (the person started typing again).
    func clearError() {
        if state.hasError {
            state = state.copyWith(errorMessage: nil)
        }
    }

    // MARK: _LoginScreenState

    /// `_submit`: local validation, then login with the success hold.
    func submit(username rawUsername: String, password: String) async {
        let username = rawUsername.trimmingCharacters(in: .whitespacesAndNewlines)
        if username.isEmpty || password.isEmpty {
            localError = "Please enter your username and password."
            return
        }
        localError = nil
        await login(username, password, holdBeforeCommit: Self.successHold)
    }

    /// `_clearErrors`: on every keystroke.
    func clearErrors() {
        if localError != nil { localError = nil }
        clearError()
    }

    /// `_resolveBiometrics`.
    func resolveBiometrics(_ service: any BiometricAuthenticating) async {
        let can = await service.canCheck()
        let types = await service.availableTypes()
        let face = types.contains(.face)
        biometricAvailable = can
        biometricIsFace = face
        biometricMethod = face ? "Face ID" : (types.contains(.fingerprint) ? "Touch ID" : "biometrics")
    }

    /// `_toggleBiometricLock`: turning it on authenticates the device owner
    /// first, so it works here without a session — the preference arms the
    /// lock for the next sign-in.
    func toggleBiometricLock(_ want: Bool, lock: AppLock) async {
        if biometricBusy { return }
        biometricBusy = true
        var failed = false
        if want {
            failed = !(await lock.enable())
        } else {
            await lock.disable()
        }
        biometricBusy = false
        if failed { localError = "Could not turn on \(biometricMethod)." }
    }
}

/// Coordinates the quiet handoff from the coin entrance to the login form —
/// `LoginEntranceController`.
@Observable
final class LoginEntranceModel {
    static let revealDelay: Duration = .milliseconds(450)

    private(set) var showForm = false

    @ObservationIgnored private var timer: Task<Void, Never>?
    @ObservationIgnored private let sleep: PollingSleep

    init(sleep: @escaping PollingSleep = realPollingSleep) {
        self.sleep = sleep
    }

    func onCoinEntranceStarted() {
        if showForm || timer != nil { return }
        let sleep = sleep
        timer = Task { [weak self] in
            do { try await sleep(Self.revealDelay) } catch { return }
            self?.showForm = true
        }
    }

    func dispose() {
        timer?.cancel()
    }
}
