import Foundation
import Observation

/// What the login coin should be doing — `CoinPhase` in `login_coin.dart`.
nonisolated enum CoinPhase: Hashable, Sendable {
    /// Resting after its entrance.
    case idle
    /// A request is in flight.
    case working
    /// Accepted — the satellites collapse inward and the coin turns over to
    /// show the IntelliStock mark on its back.
    case success
}

/// The four baked clips in `coin.glb`, one USDZ each
/// (`Resources/Coin/coin_<file>.usdz`).
nonisolated enum CoinClip: String, CaseIterable, Sendable {
    /// An eased three turns that settles exactly face-on (2.4 s, once).
    case intro = "Intro"
    /// A gentle bob (4.4 s, looping).
    case idle = "Idle"
    /// A seamless spin while signing in (2.2 s, looping).
    case spin = "Spin"
    /// The turn-over on success (1.9 s, once).
    case success = "Success"

    /// The USDZ resource name.
    var file: String { "coin_" + rawValue.lowercased() }

    /// `loopCount: 1` clips play once; the rest loop until replaced.
    var loops: Bool { self == .idle || self == .spin }
}

/// The renderer the coin model drives (the RealityKit scene in production, a
/// recorder in tests).
protocol LoginCoinPlaying: AnyObject {
    /// Plays `clip`; returns false when the renderer is not ready yet (Dart's
    /// "animation waiting for the renderer" path).
    func play(_ clip: CoinClip) -> Bool
    func pause()
}

/// The coin's behaviour — `_LoginCoinState` in `login_coin.dart`:
///
/// - plays `Intro` once when the model loads, then hands over to the looping
///   `Idle` bob after the clip's 2.4 s;
/// - loops `Spin` while signing in; plays `Success` once on acceptance;
/// - returning to idle after a REJECTED sign-in goes straight to `Idle` —
///   replaying the entrance made a wrong password look like a fresh screen;
/// - a 1.1 s kick starts the entrance even when the load callback is late, so
///   the form is never held back by the renderer;
/// - a failed load shows the fallback and still releases the form.
@Observable
final class LoginCoinModel {
    static let kickDelay: Duration = .milliseconds(1100)
    /// The `Intro` clip's length; the player reports no completion event.
    static let introLength: Duration = .milliseconds(2400)

    private(set) var phase: CoinPhase
    private(set) var loaded = false
    private(set) var failed = false
    /// Set the moment a clip is told to play. The viewer stays invisible
    /// until then, so the model's rest pose never flashes before the entrance.
    private(set) var started = false

    @ObservationIgnored weak var player: (any LoginCoinPlaying)?
    @ObservationIgnored var onEntranceStarted: (() -> Void)?

    @ObservationIgnored private var introDone = false
    @ObservationIgnored private var entranceNotified = false
    @ObservationIgnored private var kick: Task<Void, Never>?
    @ObservationIgnored private var idleTimer: Task<Void, Never>?
    @ObservationIgnored private let sleep: PollingSleep

    init(phase: CoinPhase = .idle, sleep: @escaping PollingSleep = realPollingSleep) {
        self.phase = phase
        self.sleep = sleep
    }

    /// `initState`: arm the kick.
    func start() {
        guard kick == nil else { return }
        let sleep = sleep
        kick = Task { [weak self] in
            do { try await sleep(Self.kickDelay) } catch { return }
            guard let self, !self.loaded, !self.failed, !self.started else { return }
            self.play()
        }
    }

    /// `dispose`.
    func stop() {
        kick?.cancel()
        idleTimer?.cancel()
    }

    /// `didUpdateWidget`: a phase change replays, deliberately not gated on
    /// the load (the clip call is harmless before it).
    func setPhase(_ newPhase: CoinPhase) {
        guard newPhase != phase else { return }
        phase = newPhase
        play()
    }

    /// `_onLoad`.
    func didLoad() {
        kick?.cancel()
        loaded = true
        play()
    }

    /// `_onError`: the fallback takes over and the form is released.
    func didFail() {
        kick?.cancel()
        failed = true
        notifyEntranceStarted()
    }

    /// Backgrounded: pause the clip (nobody sees the frames).
    func didEnterBackground() {
        guard started else { return }
        player?.pause()
    }

    /// Foregrounded: pick the current phase back up.
    func didBecomeActive() {
        guard started else { return }
        play()
    }

    private func play() {
        idleTimer?.cancel()
        if !started {
            started = true
            notifyEntranceStarted()
        }
        switch phase {
        case .idle:
            if introDone {
                // Back from a failed attempt: settle straight into the bob.
                _ = player?.play(.idle)
                return
            }
            introDone = true
            guard player?.play(.intro) == true else {
                // Keep the Intro queued for a late load, or a slow renderer
                // would skip straight to Idle.
                introDone = false
                return
            }
            let sleep = sleep
            idleTimer = Task { [weak self] in
                do { try await sleep(Self.introLength) } catch { return }
                guard let self, self.phase == .idle else { return }
                _ = self.player?.play(.idle)
            }
        case .working:
            _ = player?.play(.spin)
        case .success:
            _ = player?.play(.success)
        }
    }

    private func notifyEntranceStarted() {
        if entranceNotified { return }
        entranceNotified = true
        onEntranceStarted?()
    }
}
