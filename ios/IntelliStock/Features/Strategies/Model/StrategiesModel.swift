import Foundation
import Observation
import SwiftUI

/// `StrategySortField`.
nonisolated enum StrategySortField: String, CaseIterable, Sendable {
    case name, backtests, bestPnl, bestPct
}

/// The strategies list — `StrategiesController`: strategies merged with the
/// agent's best-backtest stats and the top-5, sorted and paged client-side.
@Observable
final class StrategiesModel {
    static let perPageOptions = [10, 20, 50]

    private(set) var rawStrategies: [JSONObject] = []
    private(set) var agentResults: [AgentResult] = []
    private(set) var top5: [JSONObject] = []
    private(set) var allBestByStrat = JSONObject()
    private(set) var sortField: StrategySortField = .bestPnl
    private(set) var sortAsc = false
    private(set) var page = 1
    private(set) var perPage = 20
    private(set) var loading = true
    private(set) var error: String?

    @ObservationIgnored private let repository: () -> StrategyRepository

    init(repository: @escaping () -> StrategyRepository) {
        self.repository = repository
    }

    // MARK: Load

    func fetchAll() async {
        loading = true
        error = nil
        let repo = repository()
        async let list = (try? await repo.list()) ?? []
        async let results = (try? await repo.agentResults()) ?? []
        async let top = (try? await repo.top5()) ?? []
        async let best = (try? await repo.bestPerStrategy()) ?? JSONObject()
        let (l, r, t, b) = await (list, results, top, best)
        if Task.isCancelled { loading = false; return }
        rawStrategies = l
        agentResults = r
        // Top-5 by rank ascending (missing rank sorts as 99).
        top5 = t.enumerated().sorted { a, b in
            let ra = Self.rank(a.element)
            let rb = Self.rank(b.element)
            return ra != rb ? ra < rb : a.offset < b.offset
        }.map(\.element)
        allBestByStrat = b
        loading = false
        page = 1
        error = nil
    }

    private static func rank(_ e: JSONObject) -> Int {
        e["rank"]?.double.map { Int($0) } ?? 99
    }

    // MARK: Sorting and paging

    func setSort(_ field: StrategySortField) {
        if sortField == field {
            sortAsc.toggle()
        } else {
            sortField = field
            sortAsc = false
        }
        page = 1
    }

    func setPage(_ p: Int) { page = p }

    func setPerPage(_ n: Int) {
        perPage = n
        page = 1
    }

    /// `_sortRows`.
    static func sortRows(_ rows: [StrategyListRow], _ field: StrategySortField, asc: Bool) -> [StrategyListRow] {
        rows.enumerated().sorted { a, b in
            let x = a.element
            let y = b.element
            let cmp: Int
            switch field {
            case .name: cmp = dartCompare(x.name.lowercased(), y.name.lowercased())
            case .backtests: cmp = x.runCount == y.runCount ? 0 : (x.runCount < y.runCount ? -1 : 1)
            case .bestPnl: cmp = compare(x.bestPnl ?? -.infinity, y.bestPnl ?? -.infinity)
            case .bestPct: cmp = compare(x.bestPct ?? -.infinity, y.bestPct ?? -.infinity)
            }
            let signed = asc ? cmp : -cmp
            return signed != 0 ? signed < 0 : a.offset < b.offset
        }.map(\.element)
    }

    private static func compare(_ a: Double, _ b: Double) -> Int {
        a == b ? 0 : (a < b ? -1 : 1)
    }

    var rows: [StrategyListRow] {
        let best = StrategyRepository.computeBestByStrategy(agentResults)
        let merged = StrategyRepository.mergeStrategyRows(rawStrategies, best, top5, allBestByStrat)
        return Self.sortRows(merged, sortField, asc: sortAsc)
    }

    /// One top-5 entry with its display name and sub-strategy names.
    struct Top5Entry: Identifiable {
        let id: Int
        let raw: JSONObject
        let rank: Int
        let name: String
        let subs: [String]
        let pnl: Double?
        let pct: Double?
        let strategyId: JSON?
        let backtestId: JSON?
    }

    var top5Enriched: [Top5Entry] {
        top5.enumerated().map { i, e in
            let snapshot = e["strategy_snapshot"]?.orderedObject ?? JSONObject()
            let sid = e["strategy_id"]
            let name: String = {
                if let n = snapshot["name"], !n.isNull { return n.dartDescription }
                if let sid, !sid.isNull { return "Strategy #\(sid.dartDescription)" }
                return "Strategy"
            }()
            let subs = (snapshot["strategies"]?.arrayValue ?? []).map { s -> String in
                guard s.isObject else { return "?" }
                return KalshiFormat.firstNonNull(s["strategy"], s["type"], .string("?"))
            }
            return Top5Entry(
                id: i,
                raw: e,
                rank: e["rank"]?.double.map { Int($0) } ?? 5,
                name: name,
                subs: subs,
                pnl: Self.toDouble(e["overall_profit"]),
                pct: Self.toDouble(e["pnl_percent"]),
                strategyId: sid.flatMap { $0.isNull ? nil : $0 },
                backtestId: e["backtest_id"].flatMap { $0.isNull ? nil : $0 }
            )
        }
    }

    /// `x is num ? toDouble() : double.tryParse(x?.toString() ?? '')`.
    static func toDouble(_ v: JSON?) -> Double? {
        guard let v, !v.isNull else { return nil }
        if let d = v.double { return d }
        return JSON.parseDouble(v.dartDescription)
    }

    var totalPages: Int {
        min(max(Int((Double(rows.count) / Double(perPage)).rounded(.up)), 1), 999)
    }

    var pagedRows: [StrategyListRow] {
        let all = rows
        let start = (page - 1) * perPage
        guard start < all.count, start >= 0 else { return [] }
        return Array(all[start..<min(start + perPage, all.count)])
    }

    /// The pager's up-to-five page numbers (`_PaginationBar`).
    static func pageWindow(page: Int, totalPages: Int) -> [Int] {
        let count = totalPages > 5 ? 5 : totalPages
        return (0..<count).map { i in
            if totalPages <= 5 { return i + 1 }
            if page <= 3 { return i + 1 }
            if page >= totalPages - 2 { return totalPages - 4 + i }
            return page - 2 + i
        }
    }
}

/// Rank theming (`_rankAccentColor`, `_rankTextColor`, `_rankMedal`).
nonisolated enum StrategyRank {
    static func medal(_ rank: Int) -> String {
        switch rank {
        case 1: "🥇"
        case 2: "🥈"
        case 3: "🥉"
        default: "#\(rank)"
        }
    }

    static func accent(_ rank: Int) -> Color {
        switch rank {
        case 1: Color(red: 0xF5 / 255, green: 0x9E / 255, blue: 0x0B / 255)
        case 2: Color(red: 0x94 / 255, green: 0xA3 / 255, blue: 0xB8 / 255)
        case 3: Color(red: 0xEA / 255, green: 0x58 / 255, blue: 0x0C / 255)
        default: DS.Palette.accent
        }
    }

    /// The rank's text colour: the accent, made legible on its tint.
    @MainActor static func text(_ rank: Int, in scheme: ColorScheme) -> Color {
        rank <= 3 ? DS.Palette.onTint(accent(rank), in: scheme) : DS.Palette.accent
    }
}
