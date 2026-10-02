import Foundation
import Observation
import SwiftUI

/// Everything the Learning screen renders — `LearningState`, fetched
/// together so the sections cannot disagree mid-refresh.
nonisolated struct LearningSnapshot: Sendable {
    var overview: LearningOverview?
    var findings: [LearningFinding] = []
    var funnels: [LearningFunnel] = []
    var approvals: [LearningApproval] = []
    var floors: [LearningFloor] = []
    var engineRunning = false
    var mode = "observe"
    var targets: LearningTargets?
    /// Set when SOME endpoints answered and others did not; the screen still
    /// renders what loaded.
    var partialError: String?

    /// Live approvals wait for a human indefinitely.
    var liveApprovals: [LearningApproval] { approvals.filter(\.holdsForever) }

    /// Phase 1 observes only; the screen reads this rather than assuming.
    var observeOnly: Bool { !(overview?.actsAutonomously ?? false) }

    /// Nothing loaded at all — the only case worth an error screen.
    var isEmptyFailure: Bool {
        overview == nil && findings.isEmpty && funnels.isEmpty && approvals.isEmpty && floors.isEmpty
    }

    /// `_targetsLabel`.
    var targetsLabel: String {
        guard let t = targets else { return "Documents & instances" }
        let watching = t.watchingAll ? "all" : String(t.watchedInstances.count)
        return "Documents (\(t.documentAllowlist.count) armed) · watching \(watching)"
    }
}

/// `learningStateProvider` plus the screen's actions.
@Observable
final class LearningModel {
    private(set) var state: Loadable<LearningSnapshot> = .loading
    /// An engine / mode / decision write in flight (no double submit).
    private(set) var acting = false

    @ObservationIgnored private let repository: () -> LearningRepository

    init(repository: @escaping () -> LearningRepository) {
        self.repository = repository
    }

    /// Runs one call; on failure records "label: error" and returns nil. A
    /// cancellation is rethrown: it is the screen going away, never a
    /// partial failure to show.
    private static func attempt<T: Sendable>(_ label: String, _ call: () async throws -> T) async throws -> (T?, String?) {
        do {
            return (try await call(), nil)
        } catch where error.isCancellation {
            throw CancellationError()
        } catch {
            return (nil, "\(label): \(KalshiFormat.errorText(error))")
        }
    }

    /// The provider body: seven endpoints, each failure recorded.
    static func fetch(_ repo: LearningRepository) async throws -> LearningSnapshot {
        async let ov = attempt("overview") { try await repo.overview() }
        async let fi = attempt("findings") { try await repo.findings() }
        async let fu = attempt("runs") { try await repo.funnels() }
        async let ap = attempt("approvals") { try await repo.approvals() }
        async let fl = attempt("noise floors") { try await repo.noiseFloors() }
        async let co = attempt("control") { try await repo.control() }
        async let ta = attempt("targets") { try await repo.targets() }
        let (o, f, u, a, l, c, t) = try await (ov, fi, fu, ap, fl, co, ta)
        let errors = [o.1, f.1, u.1, a.1, l.1, c.1, t.1].compactMap { $0 }
        let control = c.0
        let mode: String = {
            guard let cfg = control?["config"]?.orderedObject, let m = cfg["mode"], !m.isNull else { return "observe" }
            return m.dartDescription
        }()
        let snapshot = LearningSnapshot(
            overview: o.0,
            findings: f.0 ?? [],
            funnels: u.0 ?? [],
            approvals: a.0 ?? [],
            floors: l.0 ?? [],
            engineRunning: control?["running"] == .bool(true),
            mode: mode,
            targets: t.0,
            partialError: errors.isEmpty ? nil : errors.joined(separator: "; ")
        )
        if snapshot.isEmptyFailure, !errors.isEmpty {
            throw ApiError(message: errors.joined(separator: "; "))
        }
        return snapshot
    }

    /// `ref.refresh(learningStateProvider.future)`: data stays on screen
    /// while the refetch runs.
    func load() async {
        let repo = repository()
        do {
            let snapshot = try await Self.fetch(repo)
            // Left mid-fetch: keep what is showing.
            guard !Task.isCancelled else { return }
            state = .loaded(snapshot)
        } catch {
            if error.isCancellation || Task.isCancelled { return }
            state = .failed(error)
        }
    }

    // MARK: Actions (each returns the error text to show, if any)

    private func act(_ prefix: String, _ action: (LearningRepository) async throws -> Void) async -> String? {
        guard !acting else { return nil }
        acting = true
        defer { acting = false }
        do {
            try await action(repository())
            await load()
            return nil
        } catch {
            if error.isCancellation { return nil }
            return "\(prefix)\(KalshiFormat.errorText(error))"
        }
    }

    /// POST /learning/approvals/{id} with "approved" / "rejected".
    func decide(_ approval: LearningApproval, _ decision: String) async -> String? {
        await act("Could not record that decision: ") { try await $0.decide(approval.id, decision) }
    }

    func setRunning(_ running: Bool) async -> String? {
        await act("Could not change the engine: ") { try await $0.setRunning(running) }
    }

    func setMode(_ mode: String) async -> String? {
        await act("Could not change the mode: ") { try await $0.setMode(mode) }
    }

    /// Saves the targets sheet: the document allowlist, then the watched
    /// instances.
    func saveTargets(armed: [String], watched: [String]) async throws {
        let repo = repository()
        try await repo.setDocumentAllowlist(armed)
        try await repo.setWatchedInstances(watched)
    }

    // MARK: Presentation helpers

    /// The promotion ladder's six rungs.
    static let ladder: [(name: String, detail: String)] = [
        ("Proposed", "hypothesis pre-registered with a predicted direction"),
        ("Backtest", "paired A/B across windows, must clear the measured noise floor"),
        ("Shadow", "virtual portfolio on live quotes, no broker surface"),
        ("Paper", "a real paper instance, control-relative"),
        ("Live (capped)", "real money on a bounded book"),
        ("Live (full)", "applied to the primary live document"),
    ]

    /// `_severityColor`.
    static func severityColor(_ severity: String) -> Color {
        switch severity.lowercased() {
        case "high": DS.Palette.danger
        case "medium": DS.Palette.warning
        default: .secondary
        }
    }
}
