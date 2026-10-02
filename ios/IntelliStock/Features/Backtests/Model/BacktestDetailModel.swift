import Foundation
import Observation
import SwiftUI

/// A backtest's detail — `BacktestDetailController`: summary, graph data and
/// LLM cost, with a 3 s status poll while the run is active.
@Observable
final class BacktestDetailModel {
    let id: String

    private(set) var summary: BacktestSummary?
    private(set) var graphData: BacktestGraphData?
    private(set) var llmCost: LlmCost?
    private(set) var statusData: BacktestStatus?
    private(set) var loading = true
    private(set) var error: String?
    private(set) var llmCostLoading = false
    private(set) var llmCostError: String?
    private(set) var logsLoading = false
    private(set) var logsError: String?
    private(set) var logLines: [String] = []
    private(set) var logSource = ""

    /// Whether the status poll is on (`_pollTimer != nil`).
    private(set) var polling = false
    @ObservationIgnored private var llmCostTick = 0

    static let pollInterval: Duration = .seconds(3)

    @ObservationIgnored private let repository: () -> BacktestRepository
    @ObservationIgnored private let sleep: PollingSleep

    init(id: String, repository: @escaping () -> BacktestRepository, sleep: @escaping PollingSleep = realPollingSleep) {
        self.id = id
        self.repository = repository
        self.sleep = sleep
    }

    // MARK: Derived (BacktestDetailState getters)

    var currentStatus: String { statusData?.status ?? summary?.status ?? "unknown" }
    var progress: Num? { statusData?.progress }
    var elapsedSeconds: Num? { statusData?.timeElapsedSeconds ?? summary?.timeElapsedSeconds }
    var nexusLookback: NexusLookback? { statusData?.nexusLookback }
    var isActive: Bool { BacktestStatusKind.isActive(currentStatus) }
    var isTerminal: Bool { BacktestStatusKind.isTerminal(currentStatus) }

    // MARK: Init + poll

    /// `_init`: summary + graph data + LLM cost; start polling when active.
    func load() async {
        // A reload with data on screen (back from Playback) keeps it there:
        // no skeleton flash, and a failed reload leaves it showing.
        let hadData = summary != nil
        if !hadData { loading = true }
        error = nil
        let repo = repository()
        let id = id
        do {
            async let s = repo.summary(id)
            async let g = (try? await repo.graphData(id)) ?? BacktestGraphData(json: .null)
            async let c = (try? await repo.llmCost(id)) ?? LlmCost(json: .null)
            let (sum, graph, cost) = try await (s, g, c)
            summary = sum
            graphData = graph
            llmCost = cost
            loading = false
            error = nil
            if BacktestStatusKind.isActive(sum.status ?? "") {
                // Dart fetched, then started the timer; a terminal or paused
                // first status stopped it on the next tick. Turning the poll
                // on first lets that status stop it at once.
                polling = true
                await fetchStatus()
            }
        } catch {
            if marketsIsCancellation(error) { return }
            loading = false
            if !hadData { self.error = KalshiFormat.errorText(error) }
        }
    }

    /// The first load, then the 3 s poll while `polling` (pausing in the
    /// background). Runs until the calling task is cancelled.
    func run(lifecycle: AppLifecycle?) async {
        await load()
        while !Task.isCancelled {
            do { try await sleep(Self.pollInterval) } catch { return }
            if let lifecycle, !lifecycle.isForeground { continue }
            if polling { await tick() }
        }
    }

    func tick() async {
        llmCostTick = (llmCostTick + 1) % 10
        if llmCostTick == 0 {
            Task { await self.refreshLlmCostSilently() }
        }
        await fetchStatus()
    }

    func fetchStatus() async {
        let repo = repository()
        let id = id
        guard let s = try? await repo.status(id) else { return }
        statusData = s
        let st = (s.status ?? "").lowercased()
        if BacktestStatusKind.isTerminal(st) {
            polling = false
            async let sum = try? await repo.summary(id)
            async let g = (try? await repo.graphData(id)) ?? BacktestGraphData(json: .null)
            async let c = (try? await repo.llmCost(id)) ?? LlmCost(json: .null)
            let (sv, gv, cv) = await (sum, g, c)
            // Dart's Future.wait threw (and changed nothing) when the summary failed.
            if let sv {
                summary = sv
                graphData = gv
                llmCost = cv
            }
        } else if st == "paused" {
            polling = false
        } else {
            // Still running: refresh the summary so the tiles and the crypto
            // fees card update live.
            if let sum = try? await repo.summary(id) { summary = sum }
        }
    }

    private func refreshLlmCostSilently() async {
        if let cost = try? await repository().llmCost(id) {
            llmCost = cost
            llmCostError = nil
        }
    }

    // MARK: Public API

    func refreshLlmCost() async {
        llmCostLoading = true
        llmCostError = nil
        do {
            let cost = try await repository().llmCost(id)
            llmCost = cost
            llmCostLoading = false
        } catch {
            llmCostLoading = false
            if !marketsIsCancellation(error) { llmCostError = KalshiFormat.errorText(error) }
        }
    }

    func loadLogs() async {
        if logsLoading { return }
        logsLoading = true
        logsError = nil
        do {
            let data = try await repository().logs(id)
            logLines = (data["logs"]?.arrayValue ?? []).compactMap(\.string)
            logSource = data["source"].flatMap { $0.isNull ? nil : $0.dartDescription } ?? "db"
            logsLoading = false
        } catch {
            logsLoading = false
            if !marketsIsCancellation(error) { logsError = KalshiFormat.errorText(error) }
        }
    }

    /// Returns the error text, or nil on success.
    func performAction(_ action: String) async -> String? {
        let repo = repository()
        do {
            if action == "delete" {
                try await repo.delete(id)
                polling = false
                return nil
            }
            _ = try await repo.action(id, action)
            await fetchStatus()
            if action == "stop" {
                polling = false
            } else if action == "resume" {
                polling = true
            }
            return nil
        } catch {
            if marketsIsCancellation(error) { return nil }
            return KalshiFormat.errorText(error)
        }
    }

    /// The rerun body (same settings).
    func rerunBody() -> JSONObject? {
        guard let s = summary else { return nil }
        var pairs: [(String, JSON)] = []
        if let instanceId = s.instanceId { pairs.append(("instance_id", .string(instanceId))) }
        pairs += [
            ("stocks", .array(s.tickers.map(JSON.string))),
            ("start_date", JSON(s.startDate)),
            ("end_date", JSON(s.endDate)),
            ("granularity", .string(s.granularity ?? "60")),
            ("initial_cash", s.initialCash?.json ?? .int(100000)),
            ("emulate_fee_venue", .string(s.emulateFeeVenue ?? "default")),
        ]
        return JSONObject(pairs)
    }

    /// `rerun`: POST /backtests with the same settings; returns the new id.
    func rerun() async throws -> String? {
        guard let body = rerunBody() else { throw ApiError(message: "No summary loaded") }
        let data = try await repository().create(body)
        let raw = data["id"].flatMap { $0.isNull ? nil : $0 } ?? data["backtest_id"]
        return raw.flatMap { $0.isNull ? nil : $0.dartDescription }
    }

    // MARK: Action copy (`_actionMeta`)

    static func actionMeta(_ action: String) -> (title: String, body: String, icon: String) {
        switch action {
        case "pause": ("Pause Backtest", "The backtest will be paused and can be resumed later.", "pause_circle")
        case "resume": ("Resume Backtest", "The backtest will continue from where it was paused.", "play_circle")
        case "stop": ("Stop Backtest", "This will permanently stop the backtest.", "stop_circle")
        default: ("Delete Backtest", "This will permanently delete all results and data.", "delete_forever")
        }
    }
}

// MARK: - Presentation helpers

nonisolated enum BacktestDetailFormat {
    /// `_LogLine._levelColor`.
    static func levelColor(_ line: String) -> Color {
        let l = line.lowercased()
        if l.contains("error") || l.contains("fail") || l.contains("exception") { return DS.Palette.danger }
        if l.contains("warn") || l.contains("retry") || l.contains("skip") { return DS.Palette.warning }
        if l.contains("success") || l.contains("completed") || l.contains("profit") || l.contains("passed") { return DS.Palette.success }
        if l.contains("broker") { return DS.Palette.info }
        return .secondary
    }

    /// The crypto fee venues: (name, id, taker rate).
    static let feePlatforms: [(name: String, id: String, rate: Double)] = [
        ("Binance.US", "binanceus", 0.0002),
        ("Alpaca", "alpaca", 0.0025),
        ("Kraken", "kraken", 0.0026),
        ("Coinbase Advanced", "coinbase", 0.006),
    ]

    /// `_appliedLabel`: the venue id, else the matching rate, else the raw
    /// venue.
    static func appliedLabel(venue: String?, rate: Double) -> String {
        let v = (venue ?? "").lowercased()
        if let p = feePlatforms.first(where: { $0.id == v }) { return p.name }
        if let p = feePlatforms.first(where: { abs($0.rate - rate) < 1e-9 }) { return p.name }
        return venue ?? "—"
    }

    /// `_LlmPauseBanner._fmtBar`.
    static func fmtBar(_ s: String?) -> String {
        guard let s, !s.isEmpty else { return "unknown" }
        return s.count >= 10 ? String(s.prefix(10)) : s
    }

    /// `_fmtPausedAt`.
    static func fmtPausedAt(_ v: JSON) -> String {
        guard let dt = parseDateTime(v) else { return "unknown" }
        return fmtDateTime(dt)
    }

    /// `s.length > 360 ? s.substring(0, 359) + '…' : s`.
    static func truncateReason(_ s: String) -> String {
        s.count > 360 ? String(s.prefix(359)) + "…" : s
    }

    /// `'${x ?? fallback}'` for a `Num?` (Dart `toString()`).
    static func numText(_ n: Num?, _ fallback: String) -> String {
        n.map(\.description) ?? fallback
    }
}
