import Foundation
import Observation

/// View state for a Kalshi instance (`_State` in
/// kalshi_instance_detail_screen.dart and the four `kalshiInstance*Provider`
/// families). Live matches and orders refresh every 15 s.
@Observable
final class KalshiInstanceDetailModel {
    let instanceId: String

    private(set) var detail: Loadable<JSONObject> = .loading
    private(set) var decisions: Loadable<JSONObject> = .loading {
        didSet {
            // Grouped once per fetch, not on every second of the kickoff
            // countdown's redraw.
            let rows = KalshiPregame.rows(decisions.value)
            pregameRowsEmpty = rows.isEmpty
            pregameGames = KalshiPregame.games(rows)
        }
    }
    /// The pregame card's fixtures (`KalshiPregame.games` of the decisions).
    @ObservationIgnored private(set) var pregameGames: [[JSONObject]] = []
    /// No decision rows at all (the card's empty message).
    @ObservationIgnored private(set) var pregameRowsEmpty = true
    private(set) var live: Loadable<JSONObject> = .loading
    private(set) var orders: Loadable<JSONObject> = .loading
    private(set) var portfolio: Loadable<KalshiPortfolio>?
    private(set) var positions: Loadable<[KalshiPosition]>?

    private(set) var busy = false
    /// Decision-log cards expanded, by absolute row index.
    var expanded: Set<Int> = []
    private(set) var decPage = 0
    static let decPageSize = KalshiPregame.decPageSize
    static let liveInterval: Duration = .seconds(15)

    @ObservationIgnored private let repository: () -> KalshiRepository

    init(instanceId: String, repository: @escaping () -> KalshiRepository) {
        self.instanceId = instanceId
        self.repository = repository
    }

    var detailValue: JSONObject? { detail.value }
    var running: Bool { detailValue?["running"] == .bool(true) }
    var brokerageId: String { detailValue?["brokerage_id"].map { $0.isNull ? "" : $0.dartDescription } ?? "" }
    var isLive: Bool { detailValue?["environment"] == .string("live") }
    var title: String {
        guard let n = detailValue?["name"], !n.isNull else { return "Kalshi Instance" }
        return n.dartDescription
    }

    // MARK: Loads

    func loadAll() async {
        let repo = repository()
        let id = instanceId
        async let d = Loadable.capture { try await repo.instanceDetail(id) }
        async let dec = Loadable.capture { try await repo.instanceDecisions(id) }
        async let l = Loadable.capture { try await repo.instanceLive(id) }
        async let o = Loadable.capture { try await repo.instanceOrders(id) }
        let (dv, decv, lv, ov) = await (d, dec, l, o)
        detail = keep(dv, detail)
        decisions = keep(decv, decisions)
        live = keep(lv, live)
        orders = keep(ov, orders)
        await loadBrokerageCards()
    }

    /// The 15 s timer: live matches and orders only.
    func refreshLive() async {
        let repo = repository()
        let id = instanceId
        async let l = Loadable.capture { try await repo.instanceLive(id) }
        async let o = Loadable.capture { try await repo.instanceOrders(id) }
        let (lv, ov) = await (l, o)
        live = keep(lv, live)
        orders = keep(ov, orders)
    }

    /// `_refresh`: every family, plus the brokerage's positions.
    func refresh() async {
        await loadAll()
    }

    func poll(lifecycle: AppLifecycle) async {
        await loadAll()
        await PollingLoop(interval: { Self.liveInterval }) { [weak self] in
            await self?.refreshLive()
        }.run(lifecycle: lifecycle)
    }

    func reloadPortfolio() async {
        let bid = brokerageId
        guard !bid.isEmpty else { return }
        let repo = repository()
        let result = await Loadable.capture { try await repo.portfolio(bid) }
        if !result.marketsCancelled { portfolio = result }
    }

    private func loadBrokerageCards() async {
        let bid = brokerageId
        guard !bid.isEmpty else { return }
        let repo = repository()
        async let p = Loadable.capture { try await repo.portfolio(bid) }
        async let pos = Loadable.capture { try await repo.positions(bid) }
        let (pv, posv) = await (p, pos)
        if !pv.marketsCancelled { portfolio = pv }
        if posv.marketsCancelled {
            // a cancelled fetch changes nothing
        } else if case .failed = posv, positions?.value != nil {
            // keep the last good list
        } else {
            positions = posv
        }
    }

    /// A refetch keeps the previous value when it fails (Riverpod's
    /// `AsyncValue.value` survives an error after data).
    private func keep(_ new: Loadable<JSONObject>, _ old: Loadable<JSONObject>) -> Loadable<JSONObject> {
        if new.marketsCancelled { return old }
        if case .failed = new, let previous = old.value { return .loaded(previous) }
        return new
    }

    // MARK: Actions

    /// Start or stop; returns the error message to show, if any.
    func startStop(_ start: Bool) async -> String? {
        if busy { return nil }
        busy = true
        defer { busy = false }
        do {
            let repo = repository()
            if start {
                try await repo.startInstance(instanceId)
            } else {
                try await repo.stopInstance(instanceId)
            }
            await refresh()
            return nil
        } catch {
            if error.isCancellation { return nil }
            return KalshiFormat.errorText(error)
        }
    }

    /// `DELETE /instances/:id?force=true`. Throws so the caller can report.
    func delete() async throws {
        busy = true
        defer { busy = false }
        try await repository().deleteInstance(instanceId)
    }

    // MARK: Decision log paging

    func setPage(_ page: Int) {
        decPage = page
        expanded.removeAll()
    }

    func toggleExpanded(_ i: Int) {
        if expanded.contains(i) { expanded.remove(i) } else { expanded.insert(i) }
    }
}

// MARK: - Pure helpers (ported 1:1 from the screen)

nonisolated enum KalshiPregame {
    static let decPageSize = 8

    /// The decisions payload's rows as maps (`whereType<Map>()`).
    static func rows(_ d: JSONObject?) -> [JSONObject] {
        d?["decisions"].map(\.objectList) ?? []
    }

    /// Group by fixture (first-seen order), collapse each to one row per side,
    /// sort by kickoff (unknown kickoff last).
    static func games(_ rows: [JSONObject]) -> [[JSONObject]] {
        var order: [String] = []
        var groups: [String: [JSONObject]] = [:]
        for r in rows {
            let key = str(r["fixture_id"], r["match"])
            if groups[key] == nil { order.append(key) }
            groups[key, default: []].append(r)
        }
        let games = order.map { dedupeSides(groups[$0]!) }
        // Dart's List.sort is not stable-guaranteed, but equal kickoffs keep
        // their relative order in practice; a stable sort matches that.
        return games.enumerated().sorted { a, b in
            let ka = a.element.first?["kickoff_ts"]?.double ?? .infinity
            let kb = b.element.first?["kickoff_ts"]?.double ?? .infinity
            if ka != kb { return ka < kb }
            return a.offset < b.offset
        }.map(\.element)
    }

    /// `_dedupeSides`.
    static func dedupeSides(_ rs: [JSONObject]) -> [JSONObject] {
        let order = ["home": 0, "draw": 1, "away": 2]
        var sideOrder: [String] = []
        var bySide: [String: JSONObject] = [:]
        var everPlaced: [String: Bool] = [:]
        var placedEdge: [String: Double] = [:]
        for r in rs {
            let side = str(r["side"])
            if r["decision"] == .string("placed") {
                everPlaced[side] = true
                if let pe = r["entry_edge"]?.double { placedEdge[side] = pe }
            } else {
                everPlaced[side] = everPlaced[side] ?? false
            }
            let prev = bySide[side]
            let ts = str(r["ts"])
            let pts = str(prev?["ts"])
            if prev == nil { sideOrder.append(side) }
            if prev == nil || ts.compare(pts, options: .literal) != .orderedAscending {
                bySide[side] = r
            }
        }
        let out: [JSONObject] = sideOrder.map { side in
            var pairs = bySide[side]!.entries.map { ($0.key, $0.value) }
            func set(_ k: String, _ v: JSON) {
                if let i = pairs.firstIndex(where: { $0.0 == k }) { pairs[i].1 = v } else { pairs.append((k, v)) }
            }
            if everPlaced[side] == true { set("decision", "placed") }
            if let pe = placedEdge[side] { set("entry_edge", .double(pe)) }
            return JSONObject(pairs)
        }
        return out.enumerated().sorted { a, b in
            let oa = a.element["side"]?.string.flatMap { order[$0] } ?? 9
            let ob = b.element["side"]?.string.flatMap { order[$0] } ?? 9
            if oa != ob { return oa < ob }
            return a.offset < b.offset
        }.map(\.element)
    }

    /// `_bestEdge`: the largest non-null edge, 0 when none.
    static func bestEdge(_ sides: [JSONObject]) -> Double {
        var best = -Double.infinity
        for s in sides {
            if let e = s["edge"]?.double, e > best { best = e }
        }
        return best == -Double.infinity ? 0 : best
    }

    /// `_priceCents`: the fill average, else (fair − edge) × 100.
    static func priceCents(_ r: JSONObject) -> Int? {
        if let entry = r["entry_avg_cents"]?.double, r["entry_avg_cents"]?.isNum == true {
            return Int(dartTruncating: entry.rounded())
        }
        guard let fair = r["fused_fair"]?.double, let edge = r["edge"]?.double else { return nil }
        return Int(dartTruncating: ((fair - edge) * 100).rounded())
    }

    /// `_kickoffCountdown`: two-unit countdown to an epoch-seconds kickoff;
    /// "today" once the day arrives; "" for a clearly past day or no time.
    static func kickoffCountdown(_ ts: Double?, now: Date) -> String {
        guard let ts else { return "" }
        // A served kickoff far outside Int range saturates, never traps.
        guard var secs = Int(dartTruncating: (ts - Double(DartDateTime.millisecondsSinceEpoch(now)) / 1000).rounded()) else { return "" }
        if secs <= -86400 { return "" }
        if secs <= 0 { return "today" }
        let d = secs / 86400; secs -= d * 86400
        let h = secs / 3600; secs -= h * 3600
        let m = secs / 60
        let s = secs - m * 60
        let u: [(Int, String)] = [(d, "d"), (h, "h"), (m, "m"), (s, "s")]
        guard let i = u.firstIndex(where: { $0.0 > 0 }) else { return "today" }
        var out = ["\(u[i].0)\(u[i].1)"]
        if i + 1 < u.count, u[i + 1].0 > 0 { out.append("\(u[i + 1].0)\(u[i + 1].1)") }
        return out.joined(separator: " ")
    }

    /// `_pair`: "h/a" with a missing side as "—"; nil when both are missing.
    static func pair(_ a: JSON?, _ b: JSON?, decimals: Int) -> String? {
        func fmt(_ v: JSON?) -> String {
            guard let v, v.isNum, let d = v.double else { return "—" }
            return dartToStringAsFixed(d, decimals)
        }
        if a?.isNum != true, b?.isNum != true { return nil }
        return "\(fmt(a))/\(fmt(b))"
    }

    /// `_edgeSeries`: the `edge` values of an `edge_history` list.
    static func edgeSeries(_ raw: JSON?) -> [Double] {
        guard let raw, raw.isArray else { return [] }
        return raw.arrayValue.compactMap { $0.isObject ? $0["edge"].double : nil }
    }

    /// `_fmtTs`: "just now" / "Nm ago" / "Nh ago" / "MM/dd HH:mm" (local).
    static func fmtTs(_ ts: String?, now: Date) -> String {
        guard let ts, !ts.isEmpty, let t = DartDateTime.tryParse(ts) else { return "" }
        let secs = Int(now.timeIntervalSince(t).rounded(.towardZero))
        if secs < 60 { return "just now" }
        if secs < 3600 { return "\(secs / 60)m ago" }
        if secs < 86400 { return "\(secs / 3600)h ago" }
        let c = Calendar.current.dateComponents([.month, .day, .hour, .minute], from: t)
        func two(_ n: Int?) -> String { String(format: "%02d", n ?? 0) }
        return "\(two(c.month))/\(two(c.day)) \(two(c.hour)):\(two(c.minute))"
    }

    /// `(map['k'] ?? '').toString()`.
    static func str(_ v: JSON?) -> String {
        guard let v, !v.isNull else { return "" }
        return v.dartDescription
    }

    /// `(a ?? b ?? '').toString()`.
    static func str(_ a: JSON?, _ b: JSON?) -> String {
        if let a, !a.isNull { return a.dartDescription }
        return str(b)
    }

    /// The decision log page slice: (page, pages, start, rows).
    static func page(_ rows: [JSON], requested: Int, size: Int = KalshiPregame.decPageSize) -> (page: Int, pages: Int, start: Int, slice: [JSON]) {
        let pages = Int((Double(rows.count) / Double(size)).rounded(.up))
        let page = min(max(requested, 0), max(pages - 1, 0))
        let start = page * size
        let end = min(start + size, rows.count)
        return (page, pages, start, start < end ? Array(rows[start..<end]) : [])
    }
}
