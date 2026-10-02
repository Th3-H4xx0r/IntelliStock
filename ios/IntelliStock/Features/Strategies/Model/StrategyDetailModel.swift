import Foundation
import Observation
import SwiftUI

/// One strategy — `StrategyDetailController`: the strategy document, its
/// agent backtests and whether it is the agent's best. Read-only: the Flutter
/// app has no strategy editor.
@Observable
final class StrategyDetailModel {
    let strategyId: String

    private(set) var strategy: Strategy?
    private(set) var agentResults: [AgentResult] = []
    private(set) var agentBestId: String?
    private(set) var btSortField = "created_at"
    private(set) var btSortAsc = false
    private(set) var loading = true
    private(set) var error: String?

    @ObservationIgnored private let repository: () -> StrategyRepository

    init(strategyId: String, repository: @escaping () -> StrategyRepository) {
        self.strategyId = strategyId
        self.repository = repository
    }

    func load() async {
        loading = true
        error = nil
        let repo = repository()
        let id = strategyId
        do {
            async let s = repo.get(id)
            async let r = (try? await repo.agentResults()) ?? []
            async let b = repo.agentBest()
            let (sv, rv, bv) = try await (s, r, b)
            let raw = bv.flatMap { $0["id"].flatMap { $0.isNull ? nil : $0 } ?? $0["backtest_id"] }
            strategy = Strategy(json: .object(sv))
            agentResults = rv
            agentBestId = raw.flatMap { $0.isNull ? nil : $0.dartDescription }
            loading = false
            error = nil
        } catch {
            if marketsIsCancellation(error) { return }
            loading = false
            self.error = KalshiFormat.errorText(error)
        }
    }

    func refresh() async { await load() }

    /// No strategy on screen yet (never loaded, cut off by leaving, or
    /// failed): the screen's `.task` loads again on every appear.
    var needsLoad: Bool { strategy == nil }

    func setBtSort(_ field: String) {
        if btSortField == field {
            btSortAsc.toggle()
        } else {
            btSortField = field
            btSortAsc = false
        }
    }

    var isAgentBest: Bool {
        guard let agentBestId, let strategy else { return false }
        return agentBestId == String(strategy.id)
    }

    var strategyBacktests: [AgentResult] {
        guard let sid = strategy?.id else { return [] }
        return agentResults.filter { $0.strategyId == sid }
    }

    var sortedBacktests: [AgentResult] {
        strategyBacktests.enumerated().sorted { a, b in
            let x = a.element
            let y = b.element
            let cmp: Int
            switch btSortField {
            case "pnl": cmp = Self.compare(x.overallProfit ?? 0, y.overallProfit ?? 0)
            case "pct": cmp = Self.compare(x.pnlPercent ?? 0, y.pnlPercent ?? 0)
            default: cmp = dartCompare(x.createdAt ?? "", y.createdAt ?? "")
            }
            let signed = btSortAsc ? cmp : -cmp
            return signed != 0 ? signed < 0 : a.offset < b.offset
        }.map(\.element)
    }

    private static func compare(_ a: Double, _ b: Double) -> Int {
        a == b ? 0 : (a < b ? -1 : 1)
    }

    var bestPnlBacktest: AgentResult? {
        var best: AgentResult?
        for r in strategyBacktests where best == nil || (r.overallProfit ?? 0) > (best?.overallProfit ?? 0) {
            best = r
        }
        return best
    }

    /// `_phaseColor`.
    static func phaseColor(_ phase: String) -> Color {
        switch phase.lowercased() {
        case "pre": DS.Palette.info
        case "post": Color(red: 0xC0 / 255, green: 0x84 / 255, blue: 0xFC / 255)
        case "entry": DS.Palette.success
        case "exit": DS.Palette.danger
        default: .secondary
        }
    }
}

/// The "Backtest this strategy" sheet — `_BacktestModalState`: pick (or
/// create) an instance, link the strategy when needed, then queue a backtest.
@Observable
final class StrategyBacktestFormModel {
    /// `_granularities`: seconds → label.
    static let granularities: [(value: String, label: String)] = [
        ("86400", "1 day"), ("3600", "1 hour"), ("900", "15 min"), ("300", "5 min"), ("60", "1 min"),
    ]

    let strategyName: String
    let linkedStrategyId: Int?

    var stocks = ""
    var start = ""
    var end = ""
    var cash = "10000"
    var granularity = "86400"
    var selectedInstId = ""
    var newInstName = ""

    private(set) var loadingInsts = true
    private(set) var busy = false
    private(set) var msg = ""
    private(set) var msgOk = false
    private(set) var allInstances: [JSONObject] = []

    @ObservationIgnored private let repository: () -> StrategyRepository
    @ObservationIgnored private let now: () -> Date
    @ObservationIgnored private let pause: (Duration) async -> Void

    init(
        strategyName: String,
        linkedStrategyId: Int?,
        now: @escaping () -> Date = Date.init,
        pause: @escaping (Duration) async -> Void = { try? await Task.sleep(for: $0) },
        repository: @escaping () -> StrategyRepository
    ) {
        self.strategyName = strategyName
        self.linkedStrategyId = linkedStrategyId
        self.now = now
        self.pause = pause
        self.repository = repository
    }

    var linkedInstances: [JSONObject] {
        allInstances.filter { i in
            let sid = i["strategy_id"].flatMap { $0.isNull ? nil : $0.dartDescription }
            return sid == linkedStrategyId.map(String.init)
        }
    }

    var freeInstances: [JSONObject] {
        allInstances.filter { $0["strategy_id"]?.isNull ?? true }
    }

    static func instanceId(_ i: JSONObject) -> String { (i["id"] ?? .null).dartDescription }

    static func instanceName(_ i: JSONObject) -> String { KalshiFormat.firstNonNull(i["name"], i["id"]) }

    func loadInstances() async {
        loadingInsts = true
        do {
            allInstances = try await repository().instances()
            if let first = linkedInstances.first {
                selectedInstId = Self.instanceId(first)
            } else if let first = freeInstances.first {
                selectedInstId = Self.instanceId(first)
            } else {
                selectedInstId = ""
            }
        } catch {
            // non-critical
        }
        loadingInsts = false
    }

    private func fail(_ text: String) {
        msg = text
        msgOk = false
    }

    /// `_submit`. Returns the new backtest id once the success message has
    /// shown for 900 ms (the sheet then closes and opens it); nil otherwise.
    func submit() async -> String? {
        guard !busy else { return nil }
        let tickers = stocks.split(separator: ",", omittingEmptySubsequences: false)
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).uppercased() }
            .filter { !$0.isEmpty }
        if tickers.isEmpty { fail("At least one stock is required"); return nil }
        let s = start.trimmingCharacters(in: .whitespacesAndNewlines)
        let e = end.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { fail("Start date is required"); return nil }
        if e.isEmpty { fail("End date is required"); return nil }
        if dartCompare(s, e) >= 0 { fail("End date must be after start date"); return nil }
        if selectedInstId.isEmpty && newInstName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            fail("Instance name is required when creating a new one")
            return nil
        }

        busy = true
        msg = "Working..."
        msgOk = false
        defer { busy = false }

        let repo = repository()
        do {
            var instanceId = selectedInstId
            // Create a new instance if needed.
            if instanceId.isEmpty {
                let newId = String(100000 + DartDateTime.millisecondsSinceEpoch(now()) % 900000)
                let created = try await repo.createInstance(JSONObject([
                    ("id", .string(newId)),
                    ("name", .string(newInstName.trimmingCharacters(in: .whitespacesAndNewlines))),
                    ("run_command", .bool(false)),
                ]))
                instanceId = KalshiFormat.firstNonNull(created["id"], created["instance_id"], .string(""))
                if instanceId.isEmpty { throw ApiError(message: "Instance created but no ID returned") }
            }
            // Link the strategy unless the instance already runs it.
            let isLinked = linkedInstances.contains { Self.instanceId($0) == instanceId }
            if !isLinked, let sid = linkedStrategyId {
                try await repo.linkStrategy(instanceId, sid)
            }
            let result = try await repo.createBacktest(JSONObject([
                ("instance_id", .string(instanceId)),
                ("stocks", .array(tickers.map(JSON.string))),
                ("start_date", .string(s)),
                ("end_date", .string(e)),
                ("granularity", .string(granularity)),
                ("initial_cash", .double(JSON.parseDouble(cash) ?? 10000)),
            ]))
            let raw = result["id"].flatMap { $0.isNull ? nil : $0 } ?? result["backtest_id"]
            let btId = raw.flatMap { $0.isNull ? nil : $0.dartDescription } ?? ""
            msgOk = true
            msg = "Backtest #\(btId) queued!"
            await pause(.milliseconds(900))
            return btId
        } catch {
            if !marketsIsCancellation(error) { fail(KalshiFormat.errorText(error)) }
            return nil
        }
    }
}
