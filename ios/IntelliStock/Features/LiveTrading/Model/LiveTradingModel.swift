import Foundation
import Observation

// Ported from features/live_trading/application/live_state_notifier.dart.

/// A command's progress, shown as a floating toast (`CommandToast`).
nonisolated struct CommandToast: Hashable, Sendable {
    let commandId: String?
    let type: String
    let status: String
    var error: String?
    var result: JSONObject?

    var isTerminal: Bool { status == "completed" || status == "failed" }
    var isPending: Bool { status == "pending" || status == "running" }
}

/// The live-trading screen's state (`LiveTradingState`).
nonisolated struct LiveTradingState: Hashable, Sendable {
    var liveState: LiveState?
    var notRunning = false
    var equityHistory: PortfolioHistory?
    var positionHistoricals: [String: [HistPoint]] = [:]
    var currentRange = "1D"
    var commandToast: CommandToast?
    var fetchError: String?
    /// The ranges `equityHistory` and `positionHistoricals` were fetched for.
    /// They lag `currentRange` while a switch loads; the charts follow these,
    /// so they draw the new range in when its data lands, not on the tap.
    var equityHistoryRange: String?
    var positionHistoricalsRange: String?
}

/// The ranges the hero chart offers.
nonisolated let liveRanges = ["1D", "1W", "1M", "3M", "YTD", "1Y", "ALL"]

/// `_rangeLabel`: `today`, `this week`, … `all time`.
nonisolated func liveRangeLabel(_ r: String) -> String {
    switch r {
    case "1D": "today"
    case "1W": "this week"
    case "1M": "this month"
    case "3M": "past 3 months"
    case "YTD": "year to date"
    case "1Y": "past year"
    default: "all time"
    }
}

/// One instance's live session, polled adaptively — `LiveStateNotifier`:
/// 3 s while trading is active, 10 s otherwise; commands (halt, close,
/// manual order) go through `runCommand`, whose status is polled every
/// second until it settles.
@Observable
final class LiveTradingModel {
    static let activeInterval: Duration = .seconds(3)
    static let idleInterval: Duration = .seconds(10)
    static let commandPollEvery: Duration = .seconds(1)
    static let dismissAfter: Duration = .seconds(5)
    static let failedDismissAfter: Duration = .seconds(6)
    /// Status polls before a failure gives up (`attempt >= 29`; every poll,
    /// failed or not, counts, as in Dart).
    static let maxCommandPollAttempts = 30

    let instanceId: String
    private(set) var state: Loadable<LiveTradingState> = .loading

    @ObservationIgnored private let repository: () -> LiveRepository
    @ObservationIgnored private let sleep: PollingSleep
    @ObservationIgnored private let now: () -> Date
    @ObservationIgnored private var commandPollTask: Task<Void, Never>?
    @ObservationIgnored private var dismissTask: Task<Void, Never>?
    /// From `poll`: the command-status poll waits out the background on it.
    @ObservationIgnored private weak var lifecycle: AppLifecycle?

    init(
        instanceId: String,
        repository: @escaping () -> LiveRepository,
        sleep: @escaping PollingSleep = realPollingSleep,
        now: @escaping () -> Date = Date.init
    ) {
        self.instanceId = instanceId
        self.repository = repository
        self.sleep = sleep
        self.now = now
    }

    var value: LiveTradingState? { state.value }

    /// The poll cadence, re-read every cycle.
    var interval: Duration {
        state.value?.liveState?.tradingActive == true ? Self.activeInterval : Self.idleInterval
    }

    private func update(_ change: (inout LiveTradingState) -> Void) {
        guard var v = state.value else { return }
        change(&v)
        state = .loaded(v)
    }

    // MARK: Fetch / poll

    /// `_fetchState`: never throws. 404 → not running; an error keeps the
    /// previous data and records `fetchError`.
    private func fetchState() async -> LiveTradingState {
        let prev = state.value
        do {
            let ls = try await repository().liveState(instanceId)
            var next = state.value ?? prev ?? LiveTradingState()
            if let ls {
                next.liveState = ls
                next.notRunning = false
            } else {
                // Dart's copyWith(liveState: null) kept the previous state.
                next.notRunning = true
            }
            next.fetchError = nil
            return next
        } catch {
            var next = state.value ?? prev ?? LiveTradingState()
            next.fetchError = swingErrorText(error)
            return next
        }
    }

    /// The first load (and Retry).
    func load() async {
        let next = await fetchState()
        if Task.isCancelled, state.value == nil { return }
        state = .loaded(next)
    }

    func reload() async {
        state = .loading
        await load()
    }

    /// First load (unless loaded), then the adaptive poll, pausing in the
    /// background.
    func poll(lifecycle: AppLifecycle?, sleep pollSleep: PollingSleep? = nil) async {
        self.lifecycle = lifecycle
        if state.value == nil { await load() }
        guard !Task.isCancelled else { return }
        await PollingLoop(interval: { [weak self] in self?.interval ?? Self.idleInterval }, sleep: pollSleep ?? sleep) { [weak self] in
            await self?.pollCycle()
        }
        .run(lifecycle: lifecycle)
    }

    /// `_pollCycle` / `refreshNow`: the state, then the equity history and
    /// the position historicals. Dart fired those two and forgot them; here
    /// they are child tasks of the cycle, so they end with the poll (the
    /// view going away) instead of outliving it.
    func pollCycle() async {
        let next = await fetchState()
        state = .loaded(next)
        async let history: Void = refreshEquityHistory()
        async let historicals: Void = refreshPositionHistoricals()
        _ = await (history, historicals)
    }

    func refreshNow() async {
        await pollCycle()
    }

    func refreshEquityHistory() async {
        guard let prev = state.value else { return }
        let range = prev.currentRange
        do {
            var history = try await repository().equityHistory(instanceId, range)
            // 1D is shown relative to the device's local midnight.
            if range == "1D" { history = history.sinceLocalMidnight(now: now()) }
            // The range changed while this was in flight: a newer fetch owns
            // the chart.
            guard state.value?.currentRange == range else { return }
            update {
                $0.equityHistory = history
                $0.equityHistoryRange = range
            }
        } catch {}
    }

    func refreshPositionHistoricals() async {
        guard let prev = state.value else { return }
        // Stock only: /symbol-historicals has nothing for an OCC contract.
        let symbols = (prev.liveState?.positions ?? []).filter { !$0.isOption }.map(\.symbol)
        if symbols.isEmpty { return }
        let range = prev.currentRange
        do {
            let hist = try await repository().symbolHistoricals(symbols, range)
            guard state.value?.currentRange == range else { return }
            update {
                $0.positionHistoricals = hist
                $0.positionHistoricalsRange = range
            }
        } catch {}
    }

    func setRange(_ range: String) async {
        update { $0.currentRange = range }
        await refreshEquityHistory()
        await refreshPositionHistoricals()
    }

    // MARK: Commands

    /// Sends a live command and follows it to a terminal status.
    func runCommand(_ type: String, _ payload: JSONObject) async {
        commandPollTask?.cancel()
        dismissTask?.cancel()
        setToast(CommandToast(commandId: nil, type: type, status: "pending"))
        do {
            let result = try await repository().sendCommand(instanceId, type, payload)
            setToast(CommandToast(
                commandId: result.commandId, type: type, status: result.status,
                error: result.error, result: result.result
            ))
            if !result.isTerminal {
                pollCommandStatus(result.commandId)
            } else {
                scheduleDismiss()
                Task { await self.refreshNow() }
            }
        } catch {
            setToast(CommandToast(commandId: nil, type: type, status: "failed", error: swingErrorText(error)))
            scheduleDismiss(Self.failedDismissAfter)
        }
    }

    private func pollCommandStatus(_ commandId: String) {
        commandPollTask?.cancel()
        commandPollTask = Task { [weak self] in
            var attempt = 0
            while true {
                guard let sleep = self?.sleep else { return }
                do { try await sleep(Self.commandPollEvery) } catch { return }
                // No status polls in the background: wait for the foreground.
                let lifecycle = self?.lifecycle
                await lifecycle?.untilForeground()
                guard let self, !Task.isCancelled else { return }
                // Stop if this toast is no longer active.
                guard self.state.value?.commandToast?.commandId == commandId else { return }
                do {
                    let result = try await self.repository().commandStatus(commandId)
                    guard let toastAfter = self.state.value?.commandToast, toastAfter.commandId == commandId else { return }
                    self.setToast(CommandToast(
                        commandId: commandId, type: toastAfter.type, status: result.status,
                        error: result.error, result: result.result
                    ))
                    if result.isTerminal {
                        self.scheduleDismiss()
                        Task { await self.refreshNow() }
                        return
                    }
                } catch {
                    if Task.isCancelled { return }
                    if attempt >= Self.maxCommandPollAttempts - 1 {
                        // ~30 failed attempts: give up and say so.
                        guard let toastNow = self.state.value?.commandToast, toastNow.commandId == commandId else { return }
                        self.setToast(CommandToast(
                            commandId: commandId, type: toastNow.type, status: "failed",
                            error: "Timed out polling command"
                        ))
                        self.scheduleDismiss(Self.failedDismissAfter)
                        return
                    }
                }
                attempt += 1
            }
        }
    }

    private func setToast(_ toast: CommandToast) {
        update { $0.commandToast = toast }
    }

    func dismissToast() {
        dismissTask?.cancel()
        update { $0.commandToast = nil }
    }

    private func scheduleDismiss(_ delay: Duration = LiveTradingModel.dismissAfter) {
        dismissTask?.cancel()
        let sleep = sleep
        dismissTask = Task { [weak self] in
            do { try await sleep(delay) } catch { return }
            self?.update { $0.commandToast = nil }
        }
    }

    /// Waits for the in-flight command poll and dismissal (tests).
    func settleCommand() async {
        await commandPollTask?.value
    }
}
