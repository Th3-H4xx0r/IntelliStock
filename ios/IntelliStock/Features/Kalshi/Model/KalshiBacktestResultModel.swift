import Foundation
import Observation

/// A Kalshi backtest's results — `_S` in kalshi_backtest_result_screen.dart.
/// Re-polls every 3 s while the run is pending or running.
@Observable
final class KalshiBacktestResultModel {
    let backtestId: String

    private(set) var status: JSONObject?
    private(set) var result: JSONObject?
    var tab = "trades"
    var selectedDay: String?

    static let pollInterval: Duration = .seconds(3)

    @ObservationIgnored private let repository: () -> KalshiRepository

    init(backtestId: String, repository: @escaping () -> KalshiRepository) {
        self.backtestId = backtestId
        self.repository = repository
    }

    var statusText: String? { status.map { KalshiPregame.str($0["status"]) } }

    func load() async {
        do {
            let d = try await repository().backtestResults(backtestId)
            apply(d)
        } catch {}
    }

    /// Folds one results payload in (exposed for tests).
    func apply(_ d: JSONObject) {
        status = JSONObject([
            ("status", d["status"] ?? .null),
            ("summary", (d["summary"].flatMap { $0.isNull ? nil : $0 }) ?? .object(JSONObject())),
            ("error", d["error"] ?? .null),
            ("progress", d["progress"] ?? .null),
            ("started_at", d["started_at"] ?? .null),
            ("created_at", d["created_at"] ?? .null),
            ("finished_at", d["finished_at"] ?? .null),
        ])
        result = d["result"]?.orderedObject
        let days = daysList
        if selectedDay == nil, let last = days.last { selectedDay = last }
    }

    func poll(lifecycle: AppLifecycle) async {
        await load()
        await PollingLoop(interval: { Self.pollInterval }) { [weak self] in
            guard let self else { return }
            let st = self.status?["status"]
            if st == .string("pending") || st == .string("running") { await self.load() }
        }.run(lifecycle: lifecycle)
    }

    // MARK: Derived

    var summary: JSONObject { status?["summary"]?.orderedObject ?? JSONObject() }

    var trades: [JSONObject] { result?["trades"]?.objectList ?? [] }

    /// `_dayOf`: the UTC calendar day of an epoch-seconds kickoff, or
    /// "unknown".
    static func dayOf(_ ts: JSON?) -> String {
        let n = (ts?.isNum == true ? ts?.double.map { Int($0) } : nil) ?? 0
        if n == 0 { return "unknown" }
        let date = Date(timeIntervalSince1970: Double(n))
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let c = cal.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
    }

    /// Trades grouped by kickoff day, first-seen order.
    var byDay: [String: [JSONObject]] {
        var m: [String: [JSONObject]] = [:]
        for t in trades { m[Self.dayOf(t["kickoff"]), default: []].append(t) }
        return m
    }

    var daysList: [String] { byDay.keys.sorted { $0.compare($1, options: .literal) == .orderedAscending } }

    /// The trades for the selected day ("all" shows every trade).
    var dayTrades: [JSONObject] {
        selectedDay == "all" ? trades : (byDay[selectedDay ?? ""] ?? [])
    }

    /// The equity curve as (timestamps, dollars). The i-th point takes the
    /// i-th trade's kickoff, else an hourly placeholder.
    var equity: (timestamps: [Date], values: [Double]) {
        let ec = result?["equity_curve"]?.arrayValue ?? []
        let trades = trades
        var ts: [Date] = []
        var vals: [Double] = []
        for i in 0..<ec.count {
            let kt = i < trades.count ? Int(trades[i]["kickoff"]?.double ?? 0) : 0
            ts.append(kt > 0 ? Date(timeIntervalSince1970: Double(kt)) : Date(timeIntervalSince1970: Double(i) * 3600))
            vals.append((ec[i].double ?? 0) / 100)
        }
        return (ts, vals)
    }

    /// The chart's scrub: select the scrubbed trade's day.
    func scrubbed(_ i: Int?) {
        let trades = trades
        if let i, i < trades.count { selectedDay = Self.dayOf(trades[i]["kickoff"]) }
    }

    /// `_pickLabel`.
    static func pickLabel(_ t: JSONObject) -> String {
        func name(_ k: String, _ fallback: String) -> String {
            t[k].flatMap { $0.isNull ? nil : $0.dartDescription } ?? fallback
        }
        let s = t["side"]
        if s == .string("draw") { return "Draw" }
        if s == .string("home") { return "\(name("home", "Home")) to win" }
        if s == .string("away") { return "\(name("away", "Away")) to win" }
        return s?.dartDescription ?? "null"
    }
}
