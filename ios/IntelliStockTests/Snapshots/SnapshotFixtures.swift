import Foundation
@testable import IntelliStock

/// Made-up data for every endpoint the snapshot screens call.
///
/// Plug it into a `DataStub`: `stub.handler = { SnapshotFixtures.respond($0) }`.
/// Every figure is invented and self-consistent: the Alpaca Live account's
/// cash plus its positions add up to its balance ($5,396.11, −0.43% today),
/// and each holding's price series ends at its position's last price.
/// Timestamps are relative to `Date()`, so "2h ago" reads sensibly whenever
/// the harness runs; series are deterministic (no randomness), so two runs
/// draw the same curves.
///
/// Account ids, for routes that need one (`StockRoute(brokerageId:)`):
/// - `SnapshotFixtures.alpacaLiveId`: Alpaca Live, real money, holds AAPL 4.25 sh.
/// - `SnapshotFixtures.paperTestId`: Paper Test, Alpaca paper, holds AAPL 12 sh.
/// - `SnapshotFixtures.swingPaperId`: Swing Trade Paper, Alpaca paper, cash only.
/// - `SnapshotFixtures.kalshiId`: Kalshi Live.
nonisolated enum SnapshotFixtures {
    static let alpacaLiveId = "acct-alpaca-live"
    static let paperTestId = "acct-paper-test"
    static let swingPaperId = "acct-swing-paper"
    static let kalshiId = "acct-kalshi-live"

    /// The response for one request: status and JSON body. Unknown paths answer 404 `{"detail":"Not found"}`.
    static func respond(_ request: URLRequest) -> (status: Int, body: String) {
        let now = Date()
        let parts = request.path.split(separator: "/").map(String.init)
        let query = request.queryItems
        let method = request.method.uppercased()

        // Writes are never exercised by the snapshots; answer them plainly.
        if method != "GET" {
            return (200, json(["ok": true]))
        }

        // Feature areas add their own routes in their own files
        // (SnapshotFixtures+Ops / +Markets / +Shell), tried first, so
        // several people can extend the stubs without editing this file.
        if let hit = opsResponse(parts: parts, query: query, now: now)
            ?? marketsResponse(parts: parts, query: query, now: now)
            ?? shellResponse(parts: parts, query: query, now: now) {
            return hit
        }

        switch parts.first {
        case "brokerages":
            if parts.count == 1 { return (200, json(brokerages())) }
            return brokerage(parts: parts, query: query, now: now)
        case "symbol-historicals":
            let symbols = (query["symbols"] ?? "").split(separator: ",").map(String.init)
            let range = query["range"] ?? "1D"
            return (200, json(historicals(symbols, range: range, now: now)))
        case "symbols":
            if parts.count == 2, parts[1] == "search" {
                return (200, json(search(query["q"] ?? "")))
            }
            if parts.count == 3, parts[2] == "info" {
                return (200, json(info(parts[1])))
            }
        case "market":
            if parts.count == 2, parts[1] == "news" { return (200, json(news(now))) }
        case "status":
            return (200, json(engines()))
        case "agent":
            if parts.count == 2, parts[1] == "control" { return (200, json(agentControl(now))) }
        case "digest":
            if parts.count == 2, parts[1] == "control" { return (200, json(digestControl(now))) }
        case "nexus":
            if parts.count == 2, parts[1] == "status" { return (200, json(nexusStatus())) }
        case "instances":
            if parts.count == 1 { return (200, json(["instances": instances()])) }
            if parts.count == 2, let inst = instances().first(where: { ($0["id"] as? String) == parts[1] }) {
                return (200, json(inst))
            }
            if parts.count == 3, parts[2] == "portfolio-history" {
                return (200, json(portfolioHistory(id: parts[1], range: query["range"] ?? "1D", now: now)))
            }
            if parts.count == 3, parts[2] == "live-state" {
                return notFound
            }
        case "chatbot":
            if parts.count == 2, parts[1] == "conversations" { return (200, json(["conversations": [Any]()])) }
            if parts.count == 2, parts[1] == "tools" { return (200, json(["tools": [Any]()])) }
        case "models":
            return (200, json(["models": [Any]()]))
        case "push":
            if parts.count == 2, parts[1] == "devices" { return (200, json(["devices": [Any]()])) }
        case "notification-preferences":
            return (200, json([String: Any]()))
        case "strategies":
            return (200, json(["strategies": [Any]()]))
        default:
            break
        }
        return notFound
    }

    private static let notFound = (status: 404, body: #"{"detail":"Not found"}"#)

    // MARK: Brokerages

    private static func brokerages() -> [String: Any] {
        ["accounts": [
            ["id": alpacaLiveId, "account_name": "Alpaca Live", "brokerage_type": "alpaca", "status": "active", "alpaca_paper": false],
            ["id": paperTestId, "account_name": "Paper Test", "brokerage_type": "alpaca", "status": "active", "alpaca_paper": true],
            ["id": swingPaperId, "account_name": "Swing Trade Paper", "brokerage_type": "alpaca", "status": "active", "alpaca_paper": true],
            ["id": kalshiId, "account_name": "Kalshi Live", "brokerage_type": "kalshi", "status": "active", "kalshi_environment": "live"],
        ]]
    }

    private static func brokerage(parts: [String], query: [String: String], now: Date) -> (status: Int, body: String) {
        let id = parts[1]
        guard parts.count >= 3 else { return notFound }
        let isKalshi = id == kalshiId
        switch parts[2] {
        case "portfolio-history":
            if isKalshi { return (400, #"{"detail":"Unsupported brokerage type"}"#) }
            return (200, json(portfolioHistory(id: id, range: query["range"] ?? "1D", now: now)))
        case "positions":
            return (200, json(positions(id)))
        case "order-book":
            if isKalshi { return (200, json(["supported": false, "open": [Any](), "recent": [Any]()])) }
            return (200, json(orderBook(id, now: now)))
        case "holding-opens":
            return (200, json(holdingOpens(id, now: now)))
        case "movers":
            return (200, json(movers()))
        case "nexus-momentum":
            return (200, json(["momentum": [
                ["symbol": "XLE", "score": 0.82],
                ["symbol": "GLD", "score": 0.64],
                ["symbol": "NVDA", "score": 0.51],
                ["symbol": "GDX", "score": 0.33],
            ]]))
        case "trends":
            return (200, json(["trends": trends(status: query["status"] ?? "active", now: now)]))
        case "backfill-queue":
            return (200, json(["queue": [Any]()]))
        case "discovered":
            return (200, json(["stocks": [Any]()]))
        case "trade-contexts":
            return (200, json(["contexts": [Any]()]))
        case "nexus-outcomes":
            return (200, json(outcomes(now)))
        case "momentum-watchlist":
            return (200, json(["count": 0, "newest": [Any]()]))
        case "bot-activity":
            return (200, json(botActivity(query["symbol"] ?? "AAPL", now: now)))
        case "orders":
            return (200, json(fills(query["symbol"] ?? "AAPL", now: now)))
        case "kalshi":
            guard parts.count >= 4 else { return notFound }
            switch parts[3] {
            case "portfolio": return (200, json(kalshiPortfolio(now)))
            case "positions": return (200, json(kalshiPositions()))
            case "edges": return (200, json(["edges": [Any]()]))
            case "instances": return (200, json(["instances": [Any]()]))
            default: return notFound
            }
        default:
            return notFound
        }
    }

    // MARK: Accounts' figures

    /// Balance and today's change (percent) per account.
    private static func accountSummary(_ id: String) -> (value: Double, dayPct: Double) {
        switch id {
        case alpacaLiveId: (5_396.11, -0.43)
        case paperTestId: (9_936.22, 0.00)
        case swingPaperId: (96_880.62, 0.32)
        default: (10_000.00, 0.10)
        }
    }

    private struct Holding {
        let symbol: String
        let qty: Double
        let last: Double
        let avg: Double
        /// Today's move, percent.
        let dayPct: Double
    }

    /// Alpaca Live: $115.04 cash + these = $5,396.11.
    private static let liveHoldings: [Holding] = [
        Holding(symbol: "GLD", qty: 5.53, last: 365.02, avg: 340.10, dayPct: -0.89),
        Holding(symbol: "BIL", qty: 6.0822, last: 91.72, avg: 91.60, dayPct: 0.00),
        Holding(symbol: "GDX", qty: 11.2, last: 85.26, avg: 88.40, dayPct: -0.85),
        Holding(symbol: "XLE", qty: 15, last: 63.34, avg: 61.20, dayPct: 1.34),
        Holding(symbol: "AAPL", qty: 4.25, last: 188.15, avg: 182.50, dayPct: -0.77),
    ]

    /// Paper Test: $7,678.42 cash + AAPL 12 sh = $9,936.22.
    private static let paperHoldings: [Holding] = [
        Holding(symbol: "AAPL", qty: 12, last: 188.15, avg: 176.40, dayPct: -0.77),
    ]

    private static func holdings(_ id: String) -> (cash: Double, list: [Holding]) {
        switch id {
        case alpacaLiveId: (115.04, liveHoldings)
        case paperTestId: (7_678.42, paperHoldings)
        case swingPaperId: (96_880.62, [])
        default: (0, [])
        }
    }

    private static func positions(_ id: String) -> [String: Any] {
        let h = holdings(id)
        return [
            "cash": h.cash,
            "positions": h.list.map { p -> [String: Any] in
                let value = round2(p.qty * p.last)
                let cost = p.qty * p.avg
                let pnl = round2(value - cost)
                return [
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "marketValue": value,
                    "unrealizedPnl": pnl,
                    "unrealizedPnlPct": round2(cost > 0 ? pnl / cost * 100 : 0),
                    "lastPrice": p.last,
                    "avgEntryPrice": p.avg,
                ]
            },
        ]
    }

    private static func holdingOpens(_ id: String, now: Date) -> [String: Any] {
        let ages: [String: Double] = ["GLD": 140, "BIL": 60, "GDX": 25, "XLE": 12, "AAPL": 40]
        var opens: [String: Any] = [:]
        for p in holdings(id).list {
            opens[p.symbol] = iso(now.addingTimeInterval(-(ages[p.symbol] ?? 30) * 86_400))
        }
        return ["opens": opens]
    }

    /// The account's equity curve for `range`. 1D starts with one sample the
    /// evening before (yesterday's close), so the local-midnight re-base reads
    /// today's change against it.
    private static func portfolioHistory(id: String, range: String, now: Date) -> [String: Any] {
        let (value, dayPct) = accountSummary(id)
        let open = round2(value / (1 + dayPct / 100))
        let seed = Double(abs(id.hashValueStable % 97))
        let points: [(Date, Double)]
        if range == "1D" {
            let midnight = Calendar.current.startOfDay(for: now)
            let start = max(midnight.addingTimeInterval(300), now.addingTimeInterval(-7 * 3_600))
            var day = path(count: 78, from: start, to: now, startValue: open, endValue: value,
                           wiggle: value * 0.0035, seed: seed)
            day.insert((midnight.addingTimeInterval(-3_600), open), at: 0)
            points = day
        } else {
            let span = rangeSpan(range, now: now)
            let startValue = value * (1 - rangeDrift(range))
            points = path(count: rangeCount(range), from: now.addingTimeInterval(-span), to: now,
                          startValue: startValue, endValue: value, wiggle: value * 0.02, seed: seed)
        }
        let first = range == "1D" ? open : points.first?.1 ?? value
        return [
            "timestamps": points.map { iso($0.0) },
            "values": points.map { round2($0.1) },
            "current_value": value,
            "open_value": first,
            "change_abs": round2(value - first),
            "change_pct": round2(first != 0 ? (value - first) / first * 100 : 0),
        ]
    }

    // MARK: Orders

    private static func orderBook(_ id: String, now: Date) -> [String: Any] {
        guard id == alpacaLiveId else {
            return ["supported": true, "open": [Any](), "recent": [
                order("p1", "AAPL", "buy", "market", "filled", qty: 12, filledQty: 12, avg: 176.40, tif: "day",
                      submitted: now.addingTimeInterval(-40 * 86_400), filled: now.addingTimeInterval(-40 * 86_400)),
            ]]
        }
        let open: [[String: Any]] = [
            order("o1", "AAPL", "buy", "limit", "new", qty: 2, limit: 185.50, tif: "day",
                  submitted: now.addingTimeInterval(-2 * 3_600)),
            order("o2", "AAPL", "sell", "limit", "new", qty: 1.25, limit: 199.00, tif: "gtc",
                  submitted: now.addingTimeInterval(-2 * 86_400 - 1_800)),
        ]
        let recent: [[String: Any]] = [
            order("r1", "XLE", "buy", "market", "filled", qty: 5, filledQty: 5, avg: 62.80, tif: "day",
                  submitted: now.addingTimeInterval(-3 * 3_600), filled: now.addingTimeInterval(-3 * 3_600 + 2)),
            order("r2", "AAPL", "sell", "market", "filled", qty: 2, filledQty: 2, avg: 190.40, tif: "day",
                  submitted: now.addingTimeInterval(-26 * 3_600), filled: now.addingTimeInterval(-26 * 3_600 + 1)),
            order("r3", "GDX", "buy", "limit", "canceled", qty: 3, limit: 86.00, tif: "day",
                  submitted: now.addingTimeInterval(-2 * 86_400 - 7_200), canceled: now.addingTimeInterval(-2 * 86_400)),
            order("r4", "SPY", "buy", "market", "filled", notional: 250, filledQty: 0.33, avg: 757.60, tif: "day",
                  submitted: now.addingTimeInterval(-3 * 86_400), filled: now.addingTimeInterval(-3 * 86_400 + 1)),
        ]
        return ["supported": true, "open": open, "recent": recent]
    }

    private static func order(
        _ id: String, _ symbol: String, _ side: String, _ type: String, _ status: String,
        qty: Double? = nil, notional: Double? = nil, filledQty: Double? = nil, avg: Double? = nil,
        limit: Double? = nil, tif: String, submitted: Date, filled: Date? = nil, canceled: Date? = nil
    ) -> [String: Any] {
        var o: [String: Any] = [
            "id": id, "symbol": symbol, "side": side, "type": type, "status": status,
            "time_in_force": tif, "submitted_at": iso(submitted), "asset_class": "us_equity",
            "filled_qty": filledQty ?? 0,
        ]
        if let qty { o["qty"] = qty }
        if let notional { o["notional"] = notional }
        if let avg { o["filled_avg_price"] = avg }
        if let limit { o["limit_price"] = limit }
        if let filled { o["filled_at"] = iso(filled) }
        if let canceled { o["canceled_at"] = iso(canceled) }
        return o
    }

    /// `GET /brokerages/{id}/orders?symbol=`: recent fills for the stock screen.
    private static func fills(_ symbol: String, now: Date) -> [String: Any] {
        guard symbol == "AAPL" else { return ["orders": [Any]()] }
        return ["orders": [
            ["side": "sell", "symbol": "AAPL", "price": 190.40, "qty": 2, "ts": iso(now.addingTimeInterval(-26 * 3_600)), "order_id": "r2"],
            ["side": "buy", "symbol": "AAPL", "price": 181.10, "qty": 3, "ts": iso(now.addingTimeInterval(-12 * 86_400)), "order_id": "f2"],
            ["side": "buy", "symbol": "AAPL", "price": 183.55, "qty": 3.25, "ts": iso(now.addingTimeInterval(-40 * 86_400)), "order_id": "f3"],
        ]]
    }

    private static func botActivity(_ symbol: String, now: Date) -> [String: Any] {
        guard symbol == "AAPL" else { return ["events": [Any]()] }
        return ["events": [
            [
                "symbol": "AAPL", "side": "buy", "ts": iso(now.addingTimeInterval(-2 * 3_600)), "price": 189.40,
                "strategy": "Momentum breakout", "action_intent": "buy",
                "reason": "Closed above the 20-day high on 1.6x average volume; sector ETF confirming.",
                "score": 0.71, "override_applied": false,
                "contributors": [
                    ["strategy": "Momentum breakout", "weight": 0.5],
                    ["strategy": "Nexus links", "weight": 0.3],
                    ["strategy": "Trend filter", "weight": 0.2],
                ],
            ],
            [
                "symbol": "AAPL", "side": "sell", "ts": iso(now.addingTimeInterval(-4 * 86_400)), "price": 193.10,
                "strategy": "", "action_intent": "sell",
                "reason": "Trailing stop hit after a 4% give-back from the peak.",
                "score": 0.42, "override_applied": true, "contributors": [Any](),
            ],
        ]]
    }

    // MARK: Prices

    /// Last price and today's move (percent) per symbol. Anything else gets a
    /// stable made-up price.
    private static func quote(_ symbol: String) -> (last: Double, dayPct: Double) {
        if let h = liveHoldings.first(where: { $0.symbol == symbol }) { return (h.last, h.dayPct) }
        switch symbol {
        case "SPY": return (759.79, 0.41)
        case "QQQ": return (690.38, 0.46)
        case "DIA": return (524.41, 0.62)
        case "IWM": return (283.07, 0.39)
        case "XLP": return (81.20, 1.00)
        case "XLF": return (52.14, 0.99)
        case "XLV": return (148.30, 0.81)
        case "XLI": return (141.75, 0.74)
        case "XLU": return (83.40, 0.61)
        case "XLK": return (262.10, 0.51)
        case "XLC": return (108.62, 0.50)
        case "XLB": return (92.18, 0.46)
        case "XLRE": return (43.05, 0.13)
        case "XLY": return (221.90, -0.30)
        case "AAL": return (11.42, 1.20)
        case "AAON": return (98.10, -0.61)
        case "AAP": return (61.25, 0.33)
        case "AAXJ": return (79.80, 0.15)
        case "NVDA": return (182.44, 1.72)
        default:
            let h = Double(abs(symbol.hashValueStable % 400))
            return (20 + h, Double(abs(symbol.hashValueStable % 300)) / 100 - 1.5)
        }
    }

    private static func historicals(_ symbols: [String], range: String, now: Date) -> [String: Any] {
        var results: [String: Any] = [:]
        for sym in symbols where !sym.isEmpty {
            let (last, dayPct) = quote(sym)
            let seed = Double(abs(sym.hashValueStable % 89))
            let points: [(Date, Double)]
            if range == "1D" {
                let midnight = Calendar.current.startOfDay(for: now)
                let start = max(midnight.addingTimeInterval(300), now.addingTimeInterval(-6.5 * 3_600))
                let open = last / (1 + dayPct / 100)
                points = path(count: 78, from: start, to: now, startValue: open, endValue: last,
                              wiggle: last * 0.004, seed: seed)
            } else {
                points = path(count: rangeCount(range), from: now.addingTimeInterval(-rangeSpan(range, now: now)), to: now,
                              startValue: last * (1 - rangeDrift(range)), endValue: last,
                              wiggle: last * 0.025, seed: seed)
            }
            results[sym] = points.map { ["ts": iso($0.0), "value": round2($0.1)] }
        }
        return ["results": results]
    }

    private static func info(_ symbol: String) -> [String: Any] {
        switch symbol {
        case "AAPL":
            return [
                "symbol": "AAPL",
                "name": "Apple Inc.",
                "exchange": "NasdaqGS",
                "sector": "Technology",
                "industry": "Consumer Electronics",
                "summary": "Apple Inc. designs, manufactures, and markets smartphones, personal computers, tablets, wearables, and accessories worldwide. The company offers iPhone, a line of smartphones; Mac, a line of personal computers; iPad, a line of multi-purpose tablets; and wearables, home, and accessories comprising AirPods, Apple TV, Apple Watch, Beats products, and HomePod.",
                "previousClose": 189.61,
                "open": 189.20,
                "dayHigh": 191.52,
                "dayLow": 185.94,
                "fiftyTwoWeekHigh": 199.62,
                "fiftyTwoWeekLow": 164.08,
                "volume": 48_210_000,
                "averageVolume": 55_120_000,
                "marketCap": 2_910_000_000_000,
                "trailingPE": 31.24,
                "forwardPE": 28.10,
                "beta": 1.21,
                "targetMeanPrice": 210.50,
            ]
        case "GLD": return ["symbol": "GLD", "name": "SPDR Gold Shares", "sector": "Commodities"]
        case "BIL": return ["symbol": "BIL", "name": "SPDR Bloomberg 1-3 Month T-Bill ETF", "sector": "Fixed Income"]
        case "GDX": return ["symbol": "GDX", "name": "VanEck Gold Miners ETF", "sector": "Basic Materials"]
        case "XLE": return ["symbol": "XLE", "name": "Energy Select Sector SPDR Fund", "sector": "Energy"]
        default:
            let match = searchUniverse.first { $0.symbol == symbol }
            return ["symbol": symbol, "name": match?.name ?? symbol]
        }
    }

    // MARK: Search

    private static let searchUniverse: [(symbol: String, name: String, type: String)] = [
        ("AAPL", "Apple Inc.", "stock"),
        ("AAL", "American Airlines Group Inc.", "stock"),
        ("AAON", "AAON, Inc.", "stock"),
        ("AAP", "Advance Auto Parts, Inc.", "stock"),
        ("AAXJ", "iShares MSCI All Country Asia ex Japan ETF", "etf"),
        ("NVDA", "NVIDIA Corporation", "stock"),
        ("SPY", "SPDR S&P 500 ETF Trust", "etf"),
        ("GLD", "SPDR Gold Shares", "etf"),
    ]

    private static func search(_ q: String) -> [String: Any] {
        let needle = q.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let hits = needle.isEmpty ? [] : searchUniverse.filter {
            $0.symbol.lowercased().hasPrefix(needle) || $0.name.lowercased().contains(needle)
        }
        return ["results": hits.map { ["symbol": $0.symbol, "name": $0.name, "type": $0.type] }]
    }

    // MARK: Insights and market

    private static func news(_ now: Date) -> [String: Any] {
        ["articles": [
            ["title": "Treasury yields slip as traders weigh softer jobs data", "source": "Reuters", "url": "https://example.com/news/1", "published_at": iso(now.addingTimeInterval(-59 * 60))],
            ["title": "Gold steadies near record as central-bank buying holds up", "source": "Bloomberg", "url": "https://example.com/news/2", "published_at": iso(now.addingTimeInterval(-4 * 3_600))],
            ["title": "Energy shares lead the S&P 500 higher on supply worries", "source": "CNBC", "url": "https://example.com/news/3", "published_at": iso(now.addingTimeInterval(-5 * 3_600))],
            ["title": "Apple suppliers signal steady holiday-quarter orders", "source": "The Wall Street Journal", "url": "https://example.com/news/4", "published_at": iso(now.addingTimeInterval(-7 * 3_600))],
            ["title": "Small caps lag as rate-cut bets are pared back", "source": "MarketWatch", "url": "https://example.com/news/5", "published_at": iso(now.addingTimeInterval(-9 * 3_600))],
        ]]
    }

    private static func movers() -> [String: Any] {
        [
            "gainers": [
                ["symbol": "NXXT", "pct": 811.53, "price": 1.70],
                ["symbol": "WHLR", "pct": 133.36, "price": 0.75],
                ["symbol": "MRNOW", "pct": 95.83, "price": 0.04],
                ["symbol": "SCKT", "pct": 84.91, "price": 0.46],
                ["symbol": "NCT", "pct": 76.08, "price": 0.72],
                ["symbol": "NIVFW", "pct": 69.84, "price": 0.01],
            ],
            "losers": [
                ["symbol": "BNAI", "pct": -48.12, "price": 2.31],
                ["symbol": "LGHL", "pct": -36.40, "price": 0.19],
                ["symbol": "SNES", "pct": -31.75, "price": 1.12],
                ["symbol": "ATPC", "pct": -27.08, "price": 0.62],
                ["symbol": "KTTA", "pct": -22.90, "price": 0.88],
                ["symbol": "CJET", "pct": -19.44, "price": 3.05],
            ],
        ]
    }

    private static func trends(status: String, now: Date) -> [[String: Any]] {
        if status == "ended" {
            return [
                ["id": "t-ended-1", "name": "Regional bank stress", "status": "ended", "direction": "bearish", "strength": 0.0,
                 "affected_tickers": ["KRE", "ZION"], "ended_at": iso(now.addingTimeInterval(-3 * 86_400))],
            ]
        }
        return [
            ["id": "t1", "name": "Gold strength on central-bank buying", "status": "active", "direction": "bullish", "strength": 0.74,
             "affected_tickers": ["GLD", "GDX", "NEM", "AEM", "GOLD", "KGC"], "affected_sectors": ["Materials"]],
            ["id": "t2", "name": "Oil supply tightening", "status": "weakening", "direction": "bullish", "strength": 0.52,
             "affected_tickers": ["XLE", "XOM", "CVX"], "affected_sectors": ["Energy"],
             "reversal_articles": [["title": "OPEC+ signals output increase"]]],
            ["id": "t3", "name": "Consumer discretionary slowdown", "status": "active", "direction": "bearish", "strength": 0.38,
             "affected_tickers": ["XLY", "AMZN", "TSLA"], "affected_sectors": ["Consumer Discretionary"]],
        ]
    }

    private static func outcomes(_ now: Date) -> [String: Any] {
        let day = 86_400.0
        return [
            "hit_rate": 0.62, "n": 21, "n_correct": 13, "avg_return": 1.4,
            "recent": [
                ["symbol": "XLE", "action_intent": "buy", "latest_return": 3.2, "dominant_event_type": "supply_shock", "entry_date": ymd(now.addingTimeInterval(-6 * day))],
                ["symbol": "GDX", "action_intent": "buy", "latest_return": -1.8, "dominant_event_type": "commodity_move", "entry_date": ymd(now.addingTimeInterval(-9 * day))],
                ["symbol": "GLD", "action_intent": "buy", "latest_return": 2.4, "dominant_event_type": "macro", "entry_date": ymd(now.addingTimeInterval(-14 * day))],
            ],
        ]
    }

    // MARK: Services

    private static func engines() -> [String: Any] {
        ["engines": [
            ["id": "price_engine", "status": "stopped", "details": "Broker not started"],
            ["id": "discover_engine", "status": "stopped"],
            ["id": "ai_backtest_engine", "status": "stopped"],
            ["id": "nexus_graph_engine", "status": "stopped"],
        ]]
    }

    private static func agentControl(_ now: Date) -> [String: Any] {
        ["running": false, "paused": false, "count_today": 10, "last_run_date": ymd(now.addingTimeInterval(-86_400))]
    }

    private static func digestControl(_ now: Date) -> [String: Any] {
        let midnight = Calendar.current.startOfDay(for: now)
        return [
            "running": true,
            "last_morning_at": iso(midnight.addingTimeInterval(8 * 3_600 + 5 * 60)),
            "last_evening_at": iso(midnight.addingTimeInterval(-86_400 + 20 * 3_600 + 5 * 60)),
        ]
    }

    private static func nexusStatus() -> [String: Any] {
        ["graph_build": [
            "progress_pct": 10,
            "stages": [["message": "Selected phases 1, 2, 3, 4, 7, 8, 9, 10, 11, 12"]],
        ]]
    }

    // MARK: Kalshi

    private static func kalshiPortfolio(_ now: Date) -> [String: Any] {
        let start = now.addingTimeInterval(-24 * 3_600)
        let series = path(count: 24, from: start, to: now, startValue: 35.43, endValue: 35.23, wiggle: 0.4, seed: 7)
        return [
            "value": 35.23,
            "cash": 20.11,
            "day_change": -0.20,
            "series": series.map { ["ts": iso($0.0), "value": round2($0.1)] },
        ]
    }

    private static func kalshiPositions() -> [String: Any] {
        ["positions": [
            ["market_ticker": "KXEPLGAME-26NOV15ARSCHE-ARS", "side": "yes", "contracts": 25, "unrealized_cents": -20,
             "match": "Arsenal vs Chelsea", "pick_label": "Arsenal", "pick_logo": "", "max_payout": 25.0,
             "cost": 15.12, "current_value": 14.92, "odds_pct": 59.7],
        ]]
    }

    // MARK: Instances

    private static func instances() -> [[String: Any]] {
        [
            [
                "id": "alpaca-main", "name": "Strategy EB", "created_by": "user", "run_command": true, "crashed": false,
                "strategy_id": "200", "strategy": ["id": "200", "name": "Strategy EB"],
                "brokerage_id": alpacaLiveId,
                "brokerage": ["id": alpacaLiveId, "account_name": "Alpaca Live", "brokerage_type": "alpaca", "alpaca_paper": false],
                "granularity_time_increment": 86_400, "uptime_seconds": 22_320,
                "stocks": ["TQQQ", "GLD", "GDX", "XLE", "BIL"],
            ],
            [
                "id": "swing-paper", "name": "Swing trader (paper)", "created_by": "user", "run_command": true, "crashed": false,
                "strategy_id": "194", "strategy": ["id": "194", "name": "Swing trader"],
                "brokerage_id": swingPaperId,
                "brokerage": ["id": swingPaperId, "account_name": "Swing Trade Paper", "brokerage_type": "alpaca", "alpaca_paper": true],
                "granularity_time_increment": 900, "uptime_seconds": 3_840,
                "stocks": ["AAPL", "NVDA", "MSFT"],
            ],
            [
                "id": "f14-paper", "name": "F14 mirror", "created_by": "ai", "run_command": true, "crashed": true,
                "strategy_id": "207", "strategy": ["id": "207", "name": "Strategy F14"],
                "brokerage_id": paperTestId,
                "brokerage": ["id": paperTestId, "account_name": "Paper Test", "brokerage_type": "alpaca", "alpaca_paper": true],
                "granularity_time_increment": 3_600,
                "stocks": ["SPY", "QQQ"],
            ],
            [
                "id": "nexus-lab", "name": "Nexus lab", "created_by": "user", "run_command": false, "crashed": false,
                "strategy_id": "201", "strategy": ["id": "201", "name": "Nexus lab"],
                "brokerage_id": paperTestId,
                "brokerage": ["id": paperTestId, "account_name": "Paper Test", "brokerage_type": "alpaca", "alpaca_paper": true],
                "granularity_time_increment": 900,
                "stocks": [Any](),
            ],
        ]
    }

    // MARK: Series

    private static func rangeSpan(_ range: String, now: Date) -> TimeInterval {
        let day = 86_400.0
        switch range {
        case "1W": return 7 * day
        case "1M": return 30 * day
        case "3M": return 91 * day
        case "YTD":
            let yearStart = Calendar.current.date(from: Calendar.current.dateComponents([.year], from: now)) ?? now
            return max(now.timeIntervalSince(yearStart), 7 * day)
        case "1Y": return 365 * day
        case "ALL": return 5 * 365 * day
        default: return day
        }
    }

    private static func rangeCount(_ range: String) -> Int {
        switch range {
        case "1W": 35
        case "1M": 22
        case "3M": 63
        case "YTD": 200
        case "1Y": 252
        case "ALL": 260
        default: 78
        }
    }

    /// How far below today the range's first point sits, as a fraction.
    private static func rangeDrift(_ range: String) -> Double {
        switch range {
        case "1W": 0.012
        case "1M": 0.035
        case "3M": 0.06
        case "YTD": 0.11
        case "1Y": 0.14
        case "ALL": 0.45
        default: 0.004
        }
    }

    /// `count` evenly spaced samples from `startValue` to exactly `endValue`,
    /// with a deterministic wiggle that vanishes at both ends.
    private static func path(
        count: Int, from start: Date, to end: Date,
        startValue: Double, endValue: Double, wiggle: Double, seed: Double
    ) -> [(Date, Double)] {
        let n = max(count, 2)
        let span = end.timeIntervalSince(start)
        return (0..<n).map { i in
            let t = Double(i) / Double(n - 1)
            let base = startValue + (endValue - startValue) * t
            let noise = sin(Double(i) * 0.37 + seed) * 0.6
                + sin(Double(i) * 1.13 + seed * 2) * 0.3
                + sin(Double(i) * 2.71 + seed * 3) * 0.1
            let envelope = sin(Double.pi * t)
            return (start.addingTimeInterval(span * t), base + wiggle * noise * envelope)
        }
    }

    // MARK: Encoding

    static func json(_ object: Any) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]),
              let text = String(data: data, encoding: .utf8)
        else { return "{}" }
        return text
    }

    private static func iso(_ date: Date) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f.string(from: date)
    }

    private static func ymd(_ date: Date) -> String {
        let c = Calendar.current.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
    }

    private static func round2(_ v: Double) -> Double { (v * 100).rounded() / 100 }
}

private extension String {
    /// A hash that is the same on every run (`hashValue` is seeded per process).
    nonisolated var hashValueStable: Int {
        var h = 5381
        for b in utf8 { h = (h &* 33) &+ Int(b) }
        return h
    }
}
