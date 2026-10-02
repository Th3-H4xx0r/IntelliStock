import Foundation
import Observation

// Ported from features/instances/application/instances_controller.dart (the
// detail half) and the detail screen's pure helpers.

/// The detail screen's state — `InstanceDetailState`.
nonisolated struct InstanceDetailState: Hashable, Sendable {
    var instance: Instance?
    var backtests: [InstanceBacktestRow] = []
    var btPage = 1
    var btTotalPages = 1
    var btTotal = 0
    var btSortBy = "completed_at"
    var btSortOrder = "desc"
    var btLoading = false
    /// Backtest id → progress (0–100).
    var btProgress: [String: Int] = [:]
    var liveUptimeSecs = 0
    var errorMessage: String?
}

/// `_granLabel`: `—`, `30s`, `5m`, `1h`, `1d`.
nonisolated func instanceGranLabel(_ secs: Int?) -> String {
    guard let secs else { return "—" }
    if secs < 60 { return "\(secs)s" }
    if secs < 3600 { return "\(secs / 60)m" }
    if secs < 86400 { return "\(secs / 3600)h" }
    return "\(secs / 86400)d"
}

/// `_fmtUptime`: `—`, `1h 2m 3s`, `2m 3s`, `3s`.
nonisolated func instanceUptimeLabel(_ secs: Int) -> String {
    if secs <= 0 { return "—" }
    let h = secs / 3600
    let m = (secs % 3600) / 60
    let s = secs % 60
    if h > 0 { return "\(h)h \(m)m \(s)s" }
    if m > 0 { return "\(m)m \(s)s" }
    return "\(s)s"
}

/// Whether a backtest row is still in flight.
nonisolated func instanceBacktestIsRunning(_ status: String) -> Bool {
    ["running", "queued", "pending"].contains(status.lowercased())
}

/// Statuses that end a backtest (the progress poll refetches the page).
nonisolated let instanceBacktestTerminalStatuses: Set<String> = ["completed", "finished", "stopped", "failed", "error", "cancelled"]

/// One instance with its backtests — `InstanceDetailController`.
///
/// - Uptime ticks every second while the instance runs.
/// - While any backtest runs, its progress is polled every 3 s; a terminal
///   status refetches the page.
@Observable
final class InstanceDetailModel {
    static let btPollEvery: Duration = .seconds(3)
    static let uptimeTick: Duration = .seconds(1)

    let instanceId: String
    private(set) var state: Loadable<InstanceDetailState> = .loading

    @ObservationIgnored private let repository: () -> InstanceRepository

    init(instanceId: String, repository: @escaping () -> InstanceRepository) {
        self.instanceId = instanceId
        self.repository = repository
    }

    var value: InstanceDetailState? { state.value }

    private func update(_ change: (inout InstanceDetailState) -> Void) {
        guard var v = state.value else { return }
        change(&v)
        state = .loaded(v)
    }

    // MARK: Build

    private static func rows(_ data: JSONObject) -> [InstanceBacktestRow] {
        JSON.object(data)["backtests"].objectElements.map(InstanceBacktestRow.init(json:))
    }

    /// The first load (and Retry).
    func load() async {
        let repo = repository()
        do {
            let inst = try await repo.getInstance(instanceId)
            let btData = try await repo.listBacktests(instanceId)
            let rows = Self.rows(btData)
            var st = InstanceDetailState()
            st.instance = inst
            st.backtests = rows
            st.btTotal = (btData["total"] ?? .null).int ?? rows.count
            st.btTotalPages = (btData["total_pages"] ?? .null).int ?? 1
            st.btPage = 1
            st.liveUptimeSecs = inst.uptimeSeconds ?? 0
            state = .loaded(st)
        } catch {
            if !tradingIsCancellation(error) { state = .failed(error) }
        }
    }

    func reload() async {
        state = .loading
        await load()
    }

    // MARK: Timers (Timer.periodic in Dart)

    /// One uptime tick: +1 s while the instance runs.
    func tickUptime() {
        guard let v = state.value, v.instance?.runCommand == true else { return }
        update { $0.liveUptimeSecs += 1 }
    }

    /// The uptime ticker (`_startUptimeTicker`): every second until the
    /// calling task is cancelled. Run it from its own `.task`.
    func runUptimeTicker(sleep: PollingSleep = realPollingSleep) async {
        while !Task.isCancelled {
            do { try await sleep(Self.uptimeTick) } catch { return }
            tickUptime()
        }
    }

    /// The backtest-progress poll (`_startBtPolling`): every 3 s while a
    /// backtest runs, until the calling task is cancelled.
    func runProgressPoll(sleep: PollingSleep = realPollingSleep) async {
        while !Task.isCancelled {
            do { try await sleep(Self.btPollEvery) } catch { return }
            if hasRunningBacktests { await pollBtProgress() }
        }
    }

    var hasRunningBacktests: Bool {
        state.value?.backtests.contains { instanceBacktestIsRunning($0.status) } ?? false
    }

    /// `_pollBtProgress`: each running row's `/backtests/{id}/status`;
    /// failures skipped; a terminal status refetches the page.
    func pollBtProgress() async {
        guard let value = state.value else { return }
        let running = value.backtests.filter { instanceBacktestIsRunning($0.status) }
        if running.isEmpty { return }
        let repo = repository()
        let results: [(String, JSONObject)] = await withTaskGroup(of: (String, JSONObject)?.self) { group in
            for bt in running {
                let id = bt.id
                group.addTask { (try? await repo.getBacktestStatus(id)).map { (id, $0) } }
            }
            var out: [(String, JSONObject)] = []
            for await entry in group { if let entry { out.append(entry) } }
            return out
        }
        if Task.isCancelled { return }
        var progress = state.value?.btProgress ?? value.btProgress
        var needRefresh = false
        for (id, status) in results {
            if let p = (status["progress"] ?? .null).int { progress[id] = p }
            let s = (status["status"] ?? .null).stringOr("").lowercased()
            if instanceBacktestTerminalStatuses.contains(s) { needRefresh = true }
        }
        update { $0.btProgress = progress }
        if needRefresh { await refreshBacktests() }
    }

    // MARK: Instance

    func refreshInstance() async {
        do {
            let inst = try await repository().getInstance(instanceId)
            update {
                $0.instance = inst
                $0.liveUptimeSecs = inst.uptimeSeconds ?? 0
                $0.errorMessage = nil
            }
        } catch let error as ApiError {
            update { $0.errorMessage = error.message }
        } catch {}
    }

    /// Start when stopped, stop when running, then refresh.
    func toggleRun() async {
        guard let inst = state.value?.instance else { return }
        let repo = repository()
        do {
            if inst.runCommand {
                try await repo.stopInstance(inst.id)
            } else {
                try await repo.startInstance(inst.id)
            }
            await refreshInstance()
        } catch let error as ApiError {
            update { $0.errorMessage = error.message }
        } catch {}
    }

    // MARK: Backtests

    func refreshBacktests() async {
        guard let value = state.value else { return }
        update { $0.btLoading = true }
        do {
            let data = try await repository().listBacktests(
                instanceId, page: value.btPage, sortBy: value.btSortBy, sortOrder: value.btSortOrder
            )
            let rows = Self.rows(data)
            update {
                $0.backtests = rows
                $0.btTotal = (data["total"] ?? .null).int ?? rows.count
                $0.btTotalPages = (data["total_pages"] ?? .null).int ?? 1
                $0.btLoading = false
            }
        } catch let error as ApiError {
            update {
                $0.btLoading = false
                $0.errorMessage = error.message
            }
        } catch {
            update { $0.btLoading = false }
        }
    }

    func goToBacktestPage(_ page: Int) async {
        update { $0.btPage = page }
        await refreshBacktests()
    }

    /// The same field toggles asc/desc; a new field starts desc; page 1.
    func sortBacktests(_ field: String) async {
        update {
            let order = $0.btSortBy == field ? ($0.btSortOrder == "asc" ? "desc" : "asc") : "desc"
            $0.btSortBy = field
            $0.btSortOrder = order
            $0.btPage = 1
        }
        await refreshBacktests()
    }

    func createBacktest(stocks: [String], startDate: String, endDate: String, granularity: String = "60", initialCash: Double = 100_000) async throws {
        guard let id = state.value?.instance?.id else { return }
        try await repository().createBacktest(
            instanceId: id, stocks: stocks, startDate: startDate, endDate: endDate,
            granularity: granularity, initialCash: initialCash
        )
        await refreshBacktests()
    }

    // MARK: Stocks, links, clear state

    private var linkedId: String? { state.value?.instance?.id }

    func removeStock(_ symbol: String) async throws {
        guard let id = linkedId else { return }
        try await repository().removeStock(id, symbol)
        await refreshInstance()
    }

    func addStock(_ symbol: String) async throws {
        guard let id = linkedId else { return }
        try await repository().addStock(id, symbol)
        await refreshInstance()
    }

    func linkStrategy(_ strategyId: String) async throws {
        guard let id = linkedId else { return }
        try await repository().linkStrategy(id, strategyId)
        await refreshInstance()
    }

    func unlinkStrategy() async throws {
        guard let id = linkedId else { return }
        try await repository().unlinkStrategy(id)
        await refreshInstance()
    }

    func linkBrokerage(_ brokerageId: String) async throws {
        guard let id = linkedId else { return }
        try await repository().linkBrokerage(id, brokerageId)
        await refreshInstance()
    }

    func unlinkBrokerage() async throws {
        guard let id = linkedId else { return }
        try await repository().unlinkBrokerage(id)
        await refreshInstance()
    }

    func previewClearState(_ scope: String) async throws -> JSONObject {
        guard let id = linkedId else { return [:] }
        return try await repository().previewClearState(id, scope)
    }

    func applyClearState(_ scope: String) async throws -> JSONObject {
        guard let id = linkedId else { return [:] }
        return try await repository().applyClearState(id, scope)
    }
}

/// One clear-state scope (`_kClearScopes`).
nonisolated struct InstanceClearScope: Hashable, Sendable, Identifiable {
    let value: String
    let label: String
    let blurb: String

    var id: String { value }

    static let all: [InstanceClearScope] = [
        InstanceClearScope(
            value: "lookback_only",
            label: "Lookback only",
            blurb: "GraphNexusTradeContexts + GraphNexusOutcomes. Forces lookback re-build on next run; other instance state untouched."
        ),
        InstanceClearScope(
            value: "strategy_cache_only",
            label: "DB strategy cache only",
            blurb: "NexusStrategyCache rows for this instance (non-backtest origin). Backtest snapshots preserved."
        ),
        InstanceClearScope(
            value: "full_instance",
            label: "Full instance reset",
            blurb: "Every per-instance table — lookback, runtime state, discovery, market trends, rotation cooldown, learning cache, trade outcomes, analyst panel, strategy cache (non-backtest). Shared caches and backtest snapshots preserved."
        ),
    ]

    /// `Deleted N row(s) across N table(s).` from an apply result.
    static func successMessage(_ result: JSONObject) -> String {
        let deleted = (result["total_deleted"] ?? .null).isNull ? "0" : result["total_deleted"]!.dartDescription
        let tables = result["tables"]?.array?.count ?? 0
        return "Deleted \(deleted) row(s) across \(tables) table(s)."
    }

    /// `Total rows to delete: N`.
    static func previewTotal(_ preview: JSONObject) -> String {
        let total = (preview["total_deleted"] ?? .null).isNull ? "0" : preview["total_deleted"]!.dartDescription
        return "Total rows to delete: \(total)"
    }
}

/// The create-backtest form's validation (`_CreateBacktestDetailSheet._submit`).
nonisolated enum InstanceBacktestForm {
    /// An error message, or nil when the dates pass (string comparison).
    static func validate(start: String, end: String) -> String? {
        if start.isEmpty { return "Start date is required" }
        if end.isEmpty { return "End date is required" }
        if dartCompareStrings(start, end) >= 0 { return "End date must be after start date" }
        return nil
    }

    /// Comma-split, trimmed, upper-cased, non-empty.
    static func stocks(_ text: String) -> [String] {
        text.split(separator: ",", omittingEmptySubsequences: false)
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).uppercased() }
            .filter { !$0.isEmpty }
    }

    /// `double.tryParse(cash) ?? 100000`.
    static func cash(_ text: String) -> Double {
        JSON.parseDouble(text) ?? 100_000
    }

    /// Dart `String.compareTo` (UTF-16 code units).
    private static func dartCompareStrings(_ a: String, _ b: String) -> Int {
        let x = Array(a.utf16)
        let y = Array(b.utf16)
        for i in 0..<min(x.count, y.count) where x[i] != y[i] {
            return x[i] < y[i] ? -1 : 1
        }
        return x.count == y.count ? 0 : (x.count < y.count ? -1 : 1)
    }
}
