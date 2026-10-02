import Foundation
import Observation

/// Status classes shared by the backtest list and detail controllers.
nonisolated enum BacktestStatusKind {
    /// running / queued / pending / paused / paused_llm_critical.
    static func isActive(_ s: String) -> Bool {
        ["running", "queued", "pending", "paused", "paused_llm_critical"].contains(s.lowercased())
    }

    /// completed / finished / stopped / failed / error / cancelled.
    static func isTerminal(_ s: String) -> Bool {
        ["completed", "finished", "stopped", "failed", "error", "cancelled"].contains(s.lowercased())
    }
}

/// The backtests list — `BacktestsListController`: one page of rows plus a
/// live status overlay polled every 3 s while any row is active.
@Observable
final class BacktestsListModel {
    static let perPageOptions = [10, 15, 25, 50, 100]
    static let pollInterval: Duration = .seconds(3)

    private(set) var rows: [BacktestRow] = []
    private(set) var total = 0
    private(set) var totalPages = 1
    private(set) var page = 1
    private(set) var perPage = 15
    private(set) var sortBy = "completed_at"
    private(set) var sortOrder = "desc"
    private(set) var loading = true
    private(set) var error: String?
    /// Live status overlay keyed by backtest id.
    private(set) var statusMap: [String: BacktestStatus] = [:]

    @ObservationIgnored private let repository: () -> BacktestRepository
    @ObservationIgnored private let sleep: PollingSleep

    init(repository: @escaping () -> BacktestRepository, sleep: @escaping PollingSleep = realPollingSleep) {
        self.repository = repository
        self.sleep = sleep
    }

    /// The status the card shows: the live overlay, else the row's own.
    func liveStatus(_ row: BacktestRow) -> String {
        statusMap[row.id]?.status ?? row.status ?? ""
    }

    /// Whether the 3 s status poll should run (`_managePoll`).
    var hasActive: Bool {
        rows.contains { BacktestStatusKind.isActive(liveStatus($0)) }
    }

    // MARK: Loading

    func loadPage(page: Int? = nil) async {
        let requested = page ?? self.page
        loading = true
        error = nil
        do {
            let resp = try await repository().list(page: requested, perPage: perPage, sortBy: sortBy, sortOrder: sortOrder)
            rows = resp.backtests
            total = resp.total
            totalPages = resp.totalPages
            self.page = resp.page
            loading = false
            error = nil
        } catch {
            loading = false
            if !error.isCancellation { self.error = KalshiFormat.errorText(error) }
        }
    }

    // MARK: Polling

    /// The first load, then the status poll: every 3 s, while any row is
    /// active and the app is in the foreground, refresh the active rows
    /// (Dart's start / stop timer collapses into this one cancellable loop).
    /// Runs until the calling task is cancelled.
    func run(lifecycle: AppLifecycle?) async {
        await loadPage()
        while !Task.isCancelled {
            do { try await sleep(Self.pollInterval) } catch { return }
            if let lifecycle, !lifecycle.isForeground { continue }
            if hasActive { await pollRunning() }
        }
    }

    func pollRunning() async {
        let running = rows.filter { BacktestStatusKind.isActive(liveStatus($0)) }
        if running.isEmpty { return }
        var updated = statusMap
        var anyTerminated = false
        let repo = repository()
        await withTaskGroup(of: (String, BacktestStatus?).self) { group in
            for bt in running {
                group.addTask { (bt.id, try? await repo.status(bt.id)) }
            }
            for await (id, status) in group {
                guard let status else { continue }
                updated[id] = status
                if BacktestStatusKind.isTerminal(status.status ?? "") { anyTerminated = true }
            }
        }
        statusMap = updated
        if anyTerminated { await loadPage(page: page) }
    }

    // MARK: Public actions

    func refresh() async { await loadPage() }

    func goToPage(_ p: Int) async { await loadPage(page: p) }

    func setPerPage(_ n: Int) async {
        perPage = n
        await loadPage(page: 1)
    }

    func toggleSort(_ field: String) async {
        if sortBy == field {
            sortOrder = sortOrder == "asc" ? "desc" : "asc"
        } else {
            sortBy = field
            sortOrder = "desc"
        }
        await loadPage(page: 1)
    }

    /// Returns the error text, or nil on success.
    func performAction(_ id: String, _ action: String) async -> String? {
        let repo = repository()
        do {
            _ = try await repo.action(id, action)
            // Immediately refresh that item's status.
            if let s = try? await repo.status(id) {
                statusMap[id] = s
            }
            Task { await self.loadPage(page: self.page) }
            return nil
        } catch {
            if error.isCancellation { return nil }
            return KalshiFormat.errorText(error)
        }
    }

    // MARK: Pagination (`_Pagination._buildPages`)

    /// Page numbers with nil for an ellipsis gap.
    static func buildPages(current: Int, total: Int) -> [Int?] {
        if total <= 7 { return (0..<max(total, 0)).map { Optional($0 + 1) } }
        var set: Set<Int> = [1, total]
        for p in (current - 1)...(current + 1) where p >= 1 && p <= total { set.insert(p) }
        let sorted = set.sorted()
        var out: [Int?] = []
        for (i, p) in sorted.enumerated() {
            if i > 0, p - sorted[i - 1] > 1 { out.append(nil) }
            out.append(p)
        }
        return out
    }

    // MARK: Action copy (`_actionMeta`)

    /// (verb, body, symbol) for the list's confirm dialog.
    static func actionMeta(_ action: String) -> (verb: String, body: String, icon: String) {
        switch action {
        case "pause": ("Pause", "The backtest will be paused and can be resumed later.", "pause_circle")
        case "resume": ("Resume", "The backtest will continue from where it was paused.", "play_circle")
        default: ("Stop", "This will permanently stop the backtest.", "stop_circle")
        }
    }
}
