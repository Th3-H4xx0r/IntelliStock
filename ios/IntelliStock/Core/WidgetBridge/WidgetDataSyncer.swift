import Foundation

/// Builds the home-screen widget's portfolio list from the user's INSTANCES —
/// the same live equity the Live Trading screen shows, not the coarser
/// brokerage balance. Ported from `WidgetDataSyncer` in
/// `widget_sync_service.dart`; the dashboard runs it on entry.
///
/// Requests run one after another, as in Dart. Every failure is swallowed:
/// an instance without history is skipped, and an instance without a live
/// session simply has no positions.
final class WidgetDataSyncer {
    private let client: () -> ApiClient
    private let widgetSync: WidgetSync

    /// `client` is read on every run, so a server change is picked up.
    init(client: @escaping () -> ApiClient, widgetSync: WidgetSync) {
        self.client = client
        self.widgetSync = widgetSync
    }

    func run() async {
        let client = client()
        guard let data = try? await client.get("/instances"), data.object != nil else { return }
        let instances = data["instances"].arrayValue.filter { $0.object != nil }

        var accounts: [WidgetAccount] = []
        for inst in instances {
            let id = inst["id"].string ?? ""
            if id.isEmpty { continue }
            let name = inst["name"].string ?? ""
            do {
                let raw = try await client.get("/instances/\(id)/portfolio-history", query: ["range": "1D"])
                let history = try WidgetHistory(json: raw)
                if history.values.isEmpty, history.currentValue == nil { continue }
                let n = min(history.timestamps.count, history.values.count)

                // Positions come from live-state (the Live Trading source).
                // Best-effort: an idle instance just has none.
                var positions: [WidgetPosition] = []
                if let live = try? await client.get("/instances/\(id)/live-state"),
                   let parsed = try? Self.positions(from: live) {
                    positions = parsed
                }

                accounts.append(WidgetAccount(
                    id: id,
                    label: name.isEmpty ? id : name,
                    accountValue: history.currentValue ?? history.values.last ?? 0,
                    dayPnlAbs: history.changeAbs ?? 0,
                    dayPnlPct: history.changePct ?? 0,
                    intradayPoints: (0..<n).map { i in
                        IntradayPoint(t: Int(history.timestamps[i].dartMillis / 1000), v: history.values[i])
                    },
                    positions: positions
                ))
            } catch {
                // Instance not running / no history → skip it.
                continue
            }
        }

        if !accounts.isEmpty {
            widgetSync.syncAccounts(accounts)
        }
    }

    private static func positions(from live: JSON) throws -> [WidgetPosition] {
        guard live.object != nil else { throw WidgetCastError() }
        return try live["positions"].arrayValue.filter { $0.object != nil }.map { p in
            WidgetPosition(
                symbol: p["symbol"].string ?? "",
                qty: try dartNum(p["qty"]) ?? 0,
                marketValue: try dartNum(p["market_value"]) ?? 0,
                unrealizedPnlAbs: try dartNum(p["unrealized_pnl"]) ?? 0,
                unrealizedPnlPct: try dartNum(p["unrealized_pnl_pct"]) ?? 0
            )
        }
    }
}

/// The fields of `PortfolioHistory.fromJson` the widget needs. The full model
/// lives in `Core/Models` (data agent); this mirrors its parsing, including
/// the `as num?` casts that throw on a non-number.
nonisolated private struct WidgetHistory {
    let timestamps: [Date]
    let values: [Double]
    let currentValue: Double?
    let changeAbs: Double?
    let changePct: Double?

    init(json: JSON) throws {
        guard json.object != nil else { throw WidgetCastError() }
        timestamps = json["timestamps"].arrayValue.compactMap(Self.date)
        values = try json["values"].arrayValue.map { try dartNum($0) ?? 0 }
        currentValue = try dartNum(json["current_value"])
        changeAbs = try dartNum(json["change_abs"])
        changePct = try dartNum(json["change_pct"])
    }

    /// `PortfolioHistory._toDate`: > 1e12 is milliseconds, else seconds.
    static func date(_ v: JSON) -> Date? {
        switch v {
        case .int, .double:
            guard let n = v.double, n.isFinite else { return nil }
            let ms = n > 1_000_000_000_000 ? n.rounded(.towardZero) : (n * 1000).rounded(.towardZero)
            return Date(timeIntervalSince1970: ms / 1000)
        case .string(let s):
            return DartDateTime.tryParse(s)
        default:
            return nil
        }
    }
}

nonisolated private struct WidgetCastError: Error {}

/// Dart `(x as num?)?.toDouble()`: nil for null, throws for a non-number.
nonisolated private func dartNum(_ j: JSON) throws -> Double? {
    switch j {
    case .null: return nil
    case .int, .double: return j.double
    default: throw WidgetCastError()
    }
}
