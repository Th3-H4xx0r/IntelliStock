import Foundation
@testable import IntelliStock

/// Snapshot stub routes for Live trading, Swing, Auth, Onboarding, Connect, Chat, the More tab, the lock screen.
/// Return nil for paths this area doesn't serve; `SnapshotFixtures.respond`
/// then falls through to the shared routes. Use `SnapshotFixtures.json(_:)`
/// for bodies.
///
/// Ids served here (unique, so they never shadow another area's stubs):
/// - `SnapshotFixtures.shellLiveId`: a running live session (equity, four
///   positions with one short put, three executions, an equity history).
/// - `SnapshotFixtures.shellSwingId`: a swing and wheel instance (two
///   pending signals, one approval the broker has not picked up, a wheel book
///   of two puts and four scans).
/// - `symbol-historicals` for XLE, AMD and QCOM (the order review's chart),
///   shaped so XLE ends a few percent above its $88 strike.
/// - The chat (`chatbot/*`, `models`) only while `shellChat` is not `.off`,
///   which the chat snapshots set and reset, so other areas' screens keep
///   the shared empty chat.
nonisolated extension SnapshotFixtures {
    static let shellLiveId = "shell-live"
    static let shellSwingId = "shell-swing-lab"

    enum ShellChat: Sendable { case off, conversation, picker }

    /// Which chat the stub serves (`.off`: none, the shared routes answer).
    nonisolated(unsafe) static var shellChat: ShellChat = .off

    static func shellResponse(parts: [String], query: [String: String], now: Date) -> (status: Int, body: String)? {
        if shellChat != .off, let hit = shellChatResponse(parts: parts, now: now) { return hit }
        switch parts.first {
        case "instances" where parts.count >= 3 && parts[1] == shellLiveId:
            switch parts[2] {
            case "live-state": return (200, json(shellLiveState(now)))
            case "portfolio-history": return (200, json(shellEquityHistory(range: query["range"] ?? "1D", now: now)))
            default: return nil
            }
        case "instances" where parts.count == 2 && parts[1] == shellSwingId:
            // The swing/wheel instance itself, so its detail screen renders
            // with both lanes (paper account, running).
            return (200, json([
                "id": shellSwingId, "name": "Swing lab", "created_by": "user", "run_command": true, "crashed": false,
                "strategy_id": "205",
                "strategy": ["id": "205", "name": "Swing trader paper", "strategies": [
                    ["strategy": "strategy_swing"], ["strategy": "strategy_wheel"],
                ]],
                "brokerage_id": swingPaperId,
                "brokerage": ["id": swingPaperId, "account_name": "Swing Trade Paper", "brokerage_type": "alpaca", "alpaca_paper": true],
                "uptime_seconds": 196_000, "stocks": [String](),
            ]))
        case "instances" where parts.count >= 3 && parts[1] == shellSwingId:
            if parts.count == 4, parts[2] == "swing", parts[3] == "signals" {
                if let status = query["status"] {
                    return (200, json(["signals": shellSignals(now).filter { ($0["status"] as? String) == status }]))
                }
                return (200, json(["signals": shellSignals(now)]))
            }
            if parts.count == 3, parts[2] == "wheel" { return (200, json(shellWheel(now))) }
            return nil
        case "symbol-historicals":
            let symbols = (query["symbols"] ?? "").split(separator: ",").map(String.init)
            guard !symbols.isEmpty, symbols.allSatisfy({ shellReviewSymbols[$0] != nil }) else { return nil }
            return (200, json(shellHistoricals(symbols, range: query["range"] ?? "1D", now: now)))
        case "onboarding":
            if parts.count == 2, parts[1] == "state" {
                return (200, json(["has_completed_onboarding": false, "counts": ["models": 1, "brokerages": 1, "instances": 0]]))
            }
            return nil
        default:
            return nil
        }
    }

    // MARK: Chat

    private static func shellChatResponse(parts: [String], now: Date) -> (status: Int, body: String)? {
        switch parts.first {
        case "chatbot":
            if parts.count == 2, parts[1] == "conversations" {
                return (200, json(["conversations": [shellConversation(now, full: false)]]))
            }
            if parts.count == 3, parts[1] == "conversations" { return (200, json(shellConversation(now, full: true))) }
            if parts.count == 2, parts[1] == "tools" {
                return (200, json(["tools": [
                    ["name": "list_instances", "safety": "safe"],
                    ["name": "get_portfolio_history", "safety": "safe"],
                    ["name": "list_backtests", "safety": "safe"],
                    ["name": "stop_instance", "safety": "write"],
                    ["name": "run_backtest", "safety": "write"],
                    ["name": "delete_instance", "safety": "destructive"],
                ]]))
            }
            return nil
        case "models" where parts.count == 1:
            return (200, json(["models": [
                ["id": "m1", "name": "Sonnet", "provider": "anthropic", "model": "claude-sonnet"],
                ["id": "m2", "name": "Gemini Flash", "provider": "gemini", "model": "gemini-2.5-flash"],
            ]]))
        default:
            return nil
        }
    }

    private static func shellConversation(_ now: Date, full: Bool) -> [String: Any] {
        let picker = shellChat == .picker
        var convo: [String: Any] = [
            "id": "c1",
            "title": picker ? "New conversation" : "Paper lab vs SPY",
            "message_count": picker ? 0 : 4,
        ]
        if !picker {
            convo["model_id"] = "m1"
            convo["model_name"] = "Sonnet"
        }
        guard full, !picker else { return convo }
        convo["messages"] = [
            ["id": "u1", "role": "user", "content": "How did the paper lab do this month?",
             "created_at": shellIso(now.addingTimeInterval(-900))],
            ["id": "a1", "role": "assistant",
             "content": "The paper lab is up **3.2%** this month against SPY's 1.9%. Most of the gap came from the two energy trades in week two.",
             "tool_calls": [["id": "t1", "name": "get_portfolio_history"]],
             "blocks": [
                ["type": "stat", "label": "Win rate", "value": "58%", "detail": "14 of 24 closed trades", "trend": "up"],
                ["type": "table", "title": "Best trades",
                 "headers": [["key": "symbol", "label": "Symbol"], ["key": "pnl", "label": "P&L"], ["key": "days", "label": "Days"]],
                 "rows": [["symbol": "XLE", "pnl": "+$184.20", "days": 6], ["symbol": "AMD", "pnl": "+$96.40", "days": 3]]],
             ],
             "created_at": shellIso(now.addingTimeInterval(-880))],
            ["id": "u2", "role": "user", "content": "Pause the lab instance until Monday",
             "created_at": shellIso(now.addingTimeInterval(-120))],
            ["id": "a2", "role": "assistant", "status": "pending_confirmation",
             "pending_tool": ["id": "t2", "name": "stop_instance", "safety": "write",
                              "description": "Stops the paper-lab instance. Open orders stay at the broker.",
                              "arguments": ["instance_id": "paper-lab"]],
             "created_at": shellIso(now.addingTimeInterval(-110))],
        ]
        return convo
    }

    // MARK: Live trading

    private static func shellLiveState(_ now: Date) -> [String: Any] {
        [
            "status": "active",
            "equity": 5_412.58,
            "cash": 115.04,
            "buying_power": 230.08,
            "total_pnl": 412.58,
            "total_pnl_pct": 8.25,
            "day_pnl": -18.42,
            "day_pnl_pct": -0.34,
            "uptime_sec": 22_320,
            "trading_active": true,
            "container_stale": false,
            "positions": [
                ["symbol": "QQQ", "qty": 3, "market_value": 1_537.20, "last_price": 512.40, "avg_entry_price": 497.10,
                 "unrealized_pnl": 45.90, "unrealized_pnl_pct": 3.08, "asset_class": "us_equity", "side": "long"],
                ["symbol": "GLD", "qty": 6, "market_value": 1_446.60, "last_price": 241.10, "avg_entry_price": 244.80,
                 "unrealized_pnl": -22.20, "unrealized_pnl_pct": -1.51, "asset_class": "us_equity", "side": "long"],
                ["symbol": "XLE", "qty": 10, "market_value": 923.00, "last_price": 92.30, "avg_entry_price": 90.05,
                 "unrealized_pnl": 22.50, "unrealized_pnl_pct": 2.50, "asset_class": "us_equity", "side": "long"],
                ["symbol": "APH261002P00130000", "qty": -1, "market_value": -84.00, "last_price": 0.84, "avg_entry_price": 1.42,
                 "unrealized_pnl": 58.00, "unrealized_pnl_pct": 40.80, "asset_class": "us_option", "side": "short",
                 "multiplier": 100, "underlying": "APH", "strike": 130, "expiry": "2026-10-02"],
            ],
            "recent_trades": [
                ["side": "buy", "symbol": "QQQ", "price": 509.12, "qty": 1, "ts": shellIso(now.addingTimeInterval(-3_600)), "order_id": "o1"],
                ["side": "sell", "symbol": "TQQQ", "price": 78.40, "qty": 12, "ts": shellIso(now.addingTimeInterval(-7_200)), "order_id": "o2"],
                ["side": "buy", "symbol": "GLD", "price": 243.55, "qty": 2, "ts": shellIso(now.addingTimeInterval(-90_000)), "order_id": "o3"],
            ],
        ]
    }

    private static func shellEquityHistory(range: String, now: Date) -> [String: Any] {
        let end = 5_412.58
        let start = range == "1D" ? 5_543.80 : end * 0.94
        let span: TimeInterval = range == "1D" ? 6 * 3_600 : 30 * 86_400
        let points = shellPath(count: range == "1D" ? 72 : 60, from: now.addingTimeInterval(-span), to: now,
                               startValue: start, endValue: end, wiggle: end * 0.006, seed: 11)
        return [
            "timestamps": points.map { shellIso($0.0) },
            "values": points.map { shellRound2($0.1) },
            "current_value": end,
            "open_value": start,
            "change_abs": shellRound2(end - start),
            "change_pct": shellRound2((end - start) / start * 100),
        ]
    }

    // MARK: Swing and wheel

    private static func shellSignals(_ now: Date) -> [[String: Any]] {
        [
            [
                "id": "sig-amd", "lane": "swing", "symbol": "AMD", "session": shellYmd(now),
                "created_at": shellIso(now.addingTimeInterval(-2_700)),
                "score": 68, "recommendation": "REVIEW", "status": "pending",
                "reasoning": "Pullback to the rising 50-day on falling volume after the earnings gap. Relative strength vs SMH stays positive and the sector bid held through Tuesday's selloff, while the stop sits under the gap fill so the risk is defined before the next print.",
                "key_risks": ["Semis guide-down risk", "Gap below 140 invalidates"],
                "proposal": ["entry": 148.20, "stop": 139.50, "target": 166.00, "shares": 12],
            ],
            [
                "id": "sig-xle", "lane": "wheel", "symbol": "XLE", "session": shellYmd(now),
                "created_at": shellIso(now.addingTimeInterval(-4_800)),
                "score": 54, "recommendation": "REVIEW", "status": "pending",
                "reasoning": "IV rank 41 with support at 88; one week out.",
                "key_risks": [Any](),
                "proposal": ["contract": "XLE261120P00088000", "strike": 88, "expiry": "2026-11-20", "qty": 1,
                             "limit_price": 0.92, "premium_est": 0.95],
                "context": ["stock_price": 91.20, "otm_pct": 3.5],
            ],
            [
                "id": "sig-msft", "lane": "swing", "symbol": "MSFT", "session": shellYmd(now),
                "created_at": shellIso(now.addingTimeInterval(-5_400)),
                "decided_at": shellIso(now.addingTimeInterval(-1_620)),
                "score": 77, "recommendation": "BUY", "status": "approved",
                "reasoning": "Breakout retest held.", "key_risks": [Any](),
                "proposal": ["entry": 512.00, "stop": 496.00, "target": 548.00, "shares": 2],
            ],
        ]
    }

    private static func shellWheel(_ now: Date) -> [String: Any] {
        let session = shellYmd(now)
        return [
            "open_puts": [
                ["contract": "GLD261120P00230000", "underlying": "GLD", "strike": 230, "expiry": "2026-11-20", "qty": 1,
                 "avg_entry_price": 1.42, "current_price": 0.88, "underlying_price": 241.10, "itm_pct": -4.6, "dte": 8,
                 "collateral": 23_000, "unrealized_pl": 54.00],
                ["contract": "QQQ261114P00505000", "underlying": "QQQ", "strike": 505, "expiry": "2026-11-14", "qty": 1,
                 "avg_entry_price": 3.10, "current_price": 3.65, "underlying_price": 512.40, "itm_pct": -1.4, "dte": 2,
                 "collateral": 50_500, "unrealized_pl": -55.00],
            ],
            "collateral_total": 73_500,
            "cash": 22_480.12,
            "recent_scans": [
                ["id": "sc1", "session": session, "symbol": "XLE", "strike": 88, "expiry": "2026-11-20", "score": 54, "status": "pending"],
                ["id": "sc2", "session": session, "symbol": "GLD", "strike": 230, "expiry": "2026-11-20", "score": 61, "status": "placed"],
                ["id": "sc3", "session": session, "symbol": "TSLA", "strike": 210, "expiry": "2026-11-14", "score": 38, "status": "rejected",
                 "skip_reason": "Score under the review threshold"],
                ["id": "sc4", "session": session, "symbol": "IWM", "strike": 270, "expiry": "2026-11-20", "score": 44, "status": "rejected",
                 "skip_reason": "Collateral above the lane cap"],
            ],
        ]
    }

    // MARK: Review chart

    /// Last price and the range's opening price for the review symbols.
    private static let shellReviewSymbols: [String: (last: Double, open: Double)] = [
        "XLE": (91.20, 87.10),
        "AMD": (148.90, 146.30),
        "QCOM": (184.91, 181.20),
    ]

    private static func shellHistoricals(_ symbols: [String], range: String, now: Date) -> [String: Any] {
        var results: [String: Any] = [:]
        for (i, sym) in symbols.enumerated() {
            guard let q = shellReviewSymbols[sym] else { continue }
            let span: TimeInterval = range == "1D" ? 5 * 3_600 : 30 * 86_400
            let points = shellPath(count: 72, from: now.addingTimeInterval(-span), to: now,
                                   startValue: q.open, endValue: q.last, wiggle: q.last * 0.006, seed: Double(7 + i * 5))
            results[sym] = points.map { ["ts": shellIso($0.0), "value": shellRound2($0.1)] }
        }
        return ["results": results]
    }

    // MARK: Helpers (the shared ones are private to SnapshotFixtures.swift)

    private static func shellPath(
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
            return (start.addingTimeInterval(span * t), base + wiggle * noise * sin(Double.pi * t))
        }
    }

    private static func shellIso(_ date: Date) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f.string(from: date)
    }

    private static func shellYmd(_ date: Date) -> String {
        let c = Calendar.current.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
    }

    private static func shellRound2(_ v: Double) -> Double { (v * 100).rounded() / 100 }
}
