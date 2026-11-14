import Foundation
import Observation

/// Settings › AI analysis: which registered model explains moves, and the
/// two flagging thresholds. A change shows at once and is saved with
/// `PUT /analysis/settings`; the steppers' taps are gathered for a moment
/// into one save. A failed save puts the last saved value back.
@Observable
final class AnalysisSettingsModel {
    static let saveDelay: Duration = .milliseconds(600)
    /// One stepper tap.
    static let step = 0.5

    private(set) var settings: Loadable<AnalysisSettings> = .loading
    private(set) var saving = false
    /// The last save failure.
    var error: String?

    @ObservationIgnored private let repository: () -> AnalysisRepository
    @ObservationIgnored private let sleep: PollingSleep
    /// The value the server last confirmed.
    @ObservationIgnored private var saved: AnalysisSettings?
    @ObservationIgnored private var pending: AnalysisSettingsChange?
    @ObservationIgnored private var saveTask: Task<Void, Never>?

    init(repository: @escaping () -> AnalysisRepository, sleep: @escaping PollingSleep = realPollingSleep) {
        self.repository = repository
        self.sleep = sleep
    }

    /// The server has no `/analysis/settings` (it predates the feature).
    var isUnsupported: Bool { (settings.error as? ApiError)?.statusCode == 404 }

    func load() async {
        let result = await settings.refreshing { try await self.repository().settings() }
        if case .loaded(let value) = result { saved = value }
        settings = result
    }

    func retry() async {
        settings = .loading
        await load()
    }

    // MARK: Changes

    /// Picks the model (nil: none) and saves straight away.
    func setModel(_ id: String?) async {
        guard let current = settings.value, current.modelId != id else { return }
        stage(AnalysisSettingsChange(modelId: .some(id)))
        await flush()
    }

    /// The sensitivity stepper: clamped to 1.0–6.0, saved after `saveDelay`.
    func setSigma(_ value: Double) {
        let v = Self.clamp(value, AnalysisSettings.sigmaRange)
        guard let current = settings.value, current.sigma != v else { return }
        stage(AnalysisSettingsChange(sigma: v))
        scheduleSave()
    }

    /// The minimum-move stepper: clamped to 0.5–25, saved after `saveDelay`.
    func setMinMovePct(_ value: Double) {
        let v = Self.clamp(value, AnalysisSettings.minMoveRange)
        guard let current = settings.value, current.minMovePct != v else { return }
        stage(AnalysisSettingsChange(minMovePct: v))
        scheduleSave()
    }

    /// Rounded to the stepper's 0.1 grid and kept in range.
    nonisolated static func clamp(_ value: Double, _ range: ClosedRange<Double>) -> Double {
        let rounded = (value * 10).rounded() / 10
        return min(max(rounded, range.lowerBound), range.upperBound)
    }

    private func stage(_ change: AnalysisSettingsChange) {
        guard let current = settings.value else { return }
        pending = pending.map { $0.merging(change) } ?? change
        settings = .loaded(change.applied(to: current))
        error = nil
    }

    private func scheduleSave() {
        saveTask?.cancel()
        let sleep = sleep
        saveTask = Task { [weak self] in
            do { try await sleep(Self.saveDelay) } catch { return }
            guard !Task.isCancelled else { return }
            // The timer is done. Drop the handle WITHOUT cancelling it: this
            // is the task about to send the save, and cancelling it would
            // cancel that request, which fails silently.
            self?.saveTask = nil
            await self?.send()
        }
    }

    /// Sends whatever is staged now (Settings leaving, the model picker,
    /// tests). One save at a time; a change staged meanwhile follows it.
    func flush() async {
        saveTask?.cancel()
        saveTask = nil
        await send()
    }

    private func send() async {
        guard !saving, let change = pending, !change.isEmpty else { return }
        pending = nil
        saving = true
        do {
            let value = try await repository().updateSettings(change)
            saved = value
            // A change staged while this one was in flight stays on screen.
            settings = .loaded(pending.map { $0.applied(to: value) } ?? value)
        } catch {
            if !error.isCancellation {
                self.error = (error as? ApiError)?.message ?? error.localizedDescription
            }
            if let saved { settings = .loaded(pending.map { $0.applied(to: saved) } ?? saved) }
        }
        saving = false
        if pending != nil, saveTask == nil { await send() }
    }
}
