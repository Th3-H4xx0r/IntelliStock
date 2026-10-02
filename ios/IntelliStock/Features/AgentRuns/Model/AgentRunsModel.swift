import Foundation
import Observation

/// `AgentRunsState` in `agent_runs_controller.dart`.
nonisolated struct AgentRunsState: Equatable, Sendable {
    var runs: [AgentRun] = []
    var total = 0
    var totalPages = 1
    var page = 1
    var perPage = 20
    var control = AgentControl(json: .null)
    var busy = false
    var errorMessage: String?
    /// Set while a scheduled-resume countdown runs.
    var scheduledResumeAt: Date?
    var scheduledTotalMs = 0

    /// Elapsed share (0…1) of the countdown at `now`.
    func countdownFraction(now: Date = Date()) -> Double {
        guard let resumeAt = scheduledResumeAt, scheduledTotalMs != 0 else { return 0 }
        let remaining = Int((resumeAt.timeIntervalSince(now) * 1000).rounded(.towardZero))
        let elapsed = scheduledTotalMs - min(max(remaining, 0), scheduledTotalMs)
        return min(max(Double(elapsed) / Double(scheduledTotalMs), 0), 1)
    }

    /// Whole seconds left in the countdown at `now`.
    func countdownSecsRemaining(now: Date = Date()) -> Int {
        guard let resumeAt = scheduledResumeAt else { return 0 }
        let secs = Int(resumeAt.timeIntervalSince(now).rounded(.towardZero))
        return min(max(secs, 0), scheduledTotalMs / 1000)
    }
}

/// The AI backtest agent's runs and controls — `AgentRunsController`
/// (auto-disposed; the screen owns it): a 5 s poll plus the control actions
/// and the scheduled-resume countdown.
@Observable
final class AgentRunsModel {
    static let interval: Duration = .seconds(5)
    static let perPageOptions = [10, 20, 50, 100]

    /// `.loading` until the first fetch; `.failed` while the last fetch failed.
    private(set) var state: Loadable<AgentRunsState> = .loading
    /// Ticks every second during a countdown so the ring redraws.
    private(set) var tick = 0

    @ObservationIgnored private let repository: () -> AgentRepository
    @ObservationIgnored private let now: () -> Date
    @ObservationIgnored private let sleep: PollingSleep
    @ObservationIgnored private var countdown: Task<Void, Never>?
    /// The last good state, so a failed refresh keeps page and schedule.
    @ObservationIgnored private var last = AgentRunsState()

    init(
        repository: @escaping () -> AgentRepository,
        now: @escaping () -> Date = Date.init,
        sleep: @escaping PollingSleep = realPollingSleep
    ) {
        self.repository = repository
        self.now = now
        self.sleep = sleep
    }

    var value: AgentRunsState? { state.value }

    func poll(lifecycle: AppLifecycle?) async {
        await refreshNow()
        await PollingLoop(interval: { Self.interval }, sleep: sleep) { [weak self] in
            await self?.refreshNow()
        }
        .run(lifecycle: lifecycle)
        stop()
    }

    func stop() {
        countdown?.cancel()
        countdown = nil
    }

    /// `fetch` + `_refresh`.
    func refreshNow() async {
        let prev = state.value ?? last
        do {
            let repo = repository()
            async let page = repo.runs(page: prev.page, perPage: prev.perPage)
            async let control = repo.control()
            let (p, ctrl) = try await (page, control)

            // Resumed elsewhere while our timer ran: cancel it.
            if !ctrl.isPaused, prev.scheduledResumeAt != nil {
                cancelCountdown()
            }
            var next = state.value ?? prev
            next.runs = p.runs
            next.total = p.total
            next.totalPages = p.totalPages
            next.page = p.page
            next.control = ctrl
            next.errorMessage = nil
            set(next)
        } catch {
            if error is CancellationError { return }
            state = .failed(error)
        }
    }

    private func set(_ value: AgentRunsState) {
        last = value
        state = .loaded(value)
    }

    // MARK: Control actions

    func startAgent(specialRequest: String? = nil) async {
        await controlAction { try await $0.setControl(running: true, specialRequest: specialRequest) }
    }

    func pauseAgent() async {
        await controlAction { try await $0.setControl(paused: true) }
    }

    func resumeAgentNow() async {
        cancelCountdown()
        await controlAction { try await $0.setControl(paused: false) }
    }

    func stopAgent() async {
        cancelCountdown()
        await controlAction { try await $0.setControl(running: false) }
    }

    func forceStop(_ logId: String) async {
        await controlAction { try await $0.forceStop(logId) }
    }

    /// Resume after `minutes` (0 = now), counting down every second.
    func scheduleResume(_ minutes: Int) async {
        if minutes == 0 {
            await resumeAgentNow()
            return
        }
        let durationMs = minutes * 60 * 1000
        let resumeAt = now().addingTimeInterval(Double(durationMs) / 1000)
        guard var value = state.value else { return }
        value.scheduledResumeAt = resumeAt
        value.scheduledTotalMs = durationMs
        set(value)
        startCountdown(resumeAt)
    }

    private func startCountdown(_ resumeAt: Date) {
        countdown?.cancel()
        let sleep = sleep
        countdown = Task { [weak self] in
            while !Task.isCancelled {
                do { try await sleep(.seconds(1)) } catch { return }
                guard let self, let value = self.state.value else { return }
                if value.scheduledResumeAt == nil { return }
                self.tick += 1
                if resumeAt.timeIntervalSince(self.now()) <= 0 {
                    self.cancelCountdown()
                    await self.resumeAgentNow()
                    return
                }
            }
        }
    }

    func cancelCountdown() {
        countdown?.cancel()
        countdown = nil
        guard var value = state.value else { return }
        value.scheduledResumeAt = nil
        value.scheduledTotalMs = 0
        set(value)
    }

    func goToPage(_ page: Int) async {
        guard var value = state.value else { return }
        value.page = page
        set(value)
        await refreshNow()
    }

    func setPerPage(_ perPage: Int) async {
        guard var value = state.value else { return }
        value.perPage = perPage
        value.page = 1
        set(value)
        await refreshNow()
    }

    private func controlAction(_ action: (AgentRepository) async throws -> Void) async {
        guard var value = state.value else { return }
        value.busy = true
        value.errorMessage = nil
        set(value)
        do {
            try await action(repository())
        } catch {
            if var current = state.value {
                current.busy = false
                if !(error is CancellationError) {
                    current.errorMessage = (error as? ApiError)?.message ?? error.localizedDescription
                }
                set(current)
            }
            return
        }
        await refreshNow()
        // `fetch` kept `busy` from the action's state; the refreshed page ends it.
        if var current = state.value, current.busy {
            current.busy = false
            set(current)
        }
    }
}

/// Runs grouped by cycle — `_groupByCycle`: `cycleId ?? id`, first-seen order.
nonisolated struct AgentRunCycle: Identifiable, Sendable {
    let cycleId: String
    let startedAt: Date?
    var runs: [AgentRun]

    var id: String { cycleId }

    static func group(_ runs: [AgentRun]) -> [AgentRunCycle] {
        var order: [String] = []
        var map: [String: AgentRunCycle] = [:]
        for run in runs {
            let cid = run.cycleId ?? run.id
            if map[cid] == nil {
                order.append(cid)
                map[cid] = AgentRunCycle(cycleId: cid, startedAt: run.createdAt, runs: [])
            }
            map[cid]!.runs.append(run)
        }
        return order.map { map[$0]! }
    }
}

/// The pagination row — 1, the last page and current ±2, with `nil` for a gap.
nonisolated enum AgentRunsPagination {
    static func items(page: Int, total: Int) -> [Int?] {
        var set: Set<Int> = [1, total, page]
        for d in -2...2 {
            let v = page + d
            if v >= 1 && v <= total { set.insert(v) }
        }
        var out: [Int?] = []
        var prev: Int?
        for p in set.sorted() {
            if let prev, p - prev > 1 { out.append(nil) }
            out.append(p)
            prev = p
        }
        return out
    }
}

/// `_fmtCountdown`: `mm:ss`.
nonisolated func agentCountdownLabel(_ secs: Int) -> String {
    let m = String(secs / 60)
    let s = String(secs % 60)
    return String(repeating: "0", count: max(0, 2 - m.count)) + m + ":" + String(repeating: "0", count: max(0, 2 - s.count)) + s
}
