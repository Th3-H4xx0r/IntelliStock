import Foundation
@testable import IntelliStock

/// Snapshot stub routes for Backtests and Strategies (part of the Ops area,
/// chained from `opsResponse` in SnapshotFixtures+Ops.swift). Return nil for
/// paths this area doesn't serve.
///
/// Backtest `166220` is a running run mid Nexus lookback with a full summary
/// (the web screenshot's run), `166213` a finished crypto run with fees and
/// an LLM pause history, and every listed run answers its status. Strategy
/// `207` is the agent's best, with three sub-strategies and four backtests.
nonisolated extension SnapshotFixtures {
    static let opsRunningBacktestId = "166220"
    static let opsFinishedBacktestId = "166213"
    static let opsStrategyId = "207"

    static func opsBacktestsResponse(parts: [String], query: [String: String], now: Date) -> (status: Int, body: String)? {
        switch parts.first {
        case "backtests":
            if parts.count == 1 { return (200, json(opsBacktestList(now))) }
            if parts.count == 2, parts[1] == "best-per-strategy" { return (200, json(["by_strategy": [String: Any]()])) }
            guard parts.count == 3, let row = opsBacktestRows(now).first(where: { ($0["id"] as? String) == parts[1] }) else { return nil }
            let id = parts[1]
            switch parts[2] {
            case "status": return (200, json(opsBacktestStatus(row)))
            case "summary": return (200, json(opsBacktestSummary(id, row: row)))
            case "graph-data": return (200, json(opsGraphData(id, now: now)))
            case "llm-cost": return (200, json(opsLlmCost(id)))
            case "logs": return (200, json(opsLogs(id)))
            case "playback-data": return (200, json(opsPlayback()))
            default: return nil
            }
        case "strategies":
            if parts.count == 1 { return (200, json(["strategies": opsStrategies()])) }
            if parts.count == 2, let s = opsStrategies().first(where: { "\($0["id"] ?? "")" == parts[1] }) {
                return (200, json(["strategy": s]))
            }
            return nil
        case "agent":
            guard parts.count == 2 else { return nil }
            switch parts[1] {
            case "results": return (200, json(["results": opsAgentResults(now)]))
            case "top5": return (200, json(["top5": opsTop5()]))
            case "best": return (200, json(["id": 207, "backtest_id": "166171", "overall_profit": 4_210.55]))
            default: return nil
            }
        default:
            return nil
        }
    }

    // MARK: Backtest list

    private static func opsBacktestRows(_ now: Date) -> [[String: Any]] {
        func iso(_ hoursAgo: Double) -> String {
            ISO8601DateFormatter().string(from: now.addingTimeInterval(-hoursAgo * 3_600))
        }
        let instances = ["strategy-eb-lab", "f14-paper", "alpaca-main", "strategy-x", "test"]
        let books: [[String]] = [
            ["QQQ", "GLD", "XLE"], ["TQQQ", "GLD", "GDX", "XLE", "BIL", "SPY"], ["NVDA", "MSFT", "AAPL"], ["SPY"],
            ["AMD", "AVGO", "SNDK", "MU", "NVDA", "TSM"], ["BTC/USD", "ETH/USD"],
        ]
        let periods = [("2024-01-02", "2024-12-31"), ("2022-01-03", "2022-12-30"), ("2025-03-03", "2025-09-30"), ("2020-02-18", "2020-06-30")]
        let statuses = ["running", "paused_llm_critical", "completed", "completed", "queued", "completed", "failed", "completed", "stopped", "completed", "completed", "paused", "completed", "completed", "completed"]
        let pnls: [Double?] = [4_750.71, nil, 1_204.18, -318.40, nil, 642.10, nil, 2_310.55, -95.20, 4_210.55, -1_022.80, 88.12, 330.00, -12.40, 905.33]
        return statuses.indices.map { i in
            let id = String(166_220 - i * 7)
            let (start, end) = periods[i % periods.count]
            var row: [String: Any] = [
                "id": id,
                "instance_id": instances[i % instances.count],
                "status": statuses[i],
                "stocks": books[i % books.count],
                "start_date": start,
                "end_date": end,
            ]
            if let pnl = pnls[i] {
                row["pnl"] = pnl
                row["pnl_percent"] = pnl / 100
            }
            if ["completed", "failed", "stopped"].contains(statuses[i]) {
                row["completed_at"] = iso(Double(i) * 5 + 1)
                row["time_elapsed_seconds"] = 1_200 + i * 173
            }
            return row
        }
    }

    private static func opsBacktestList(_ now: Date) -> [String: Any] {
        ["backtests": opsBacktestRows(now), "total": 48, "total_pages": 4, "page": 1]
    }

    private static func opsBacktestStatus(_ row: [String: Any]) -> [String: Any] {
        let status = row["status"] as? String ?? "completed"
        var out: [String: Any] = ["status": status]
        switch status {
        case "running":
            out["progress"] = 62
            out["time_elapsed_seconds"] = 2_140
            if row["id"] as? String == opsRunningBacktestId {
                out["nexus_lookback"] = ["current": 41, "total": 90, "current_date": "2023-10-27", "start_date": "2023-09-01", "end_date": "2023-12-29"]
            }
        case "queued":
            out["progress"] = 0
        case "paused", "paused_llm_critical":
            out["progress"] = 37
            out["time_elapsed_seconds"] = 1_520
        default:
            break
        }
        return out
    }

    // MARK: Backtest detail

    private static func opsBacktestSummary(_ id: String, row: [String: Any]) -> [String: Any] {
        if id == opsFinishedBacktestId {
            return [
                "id": id, "status": "paused_llm_critical", "instance_id": "f14-paper", "strategy_id": "207",
                "pnl": 642.10, "pnl_percent": 6.42, "portfolio_start_value": 10_000, "portfolio_end_value": 10_642.10,
                "total_trades": 18, "total_buys": 9, "total_sells": 9, "time_elapsed_seconds": 1_888,
                "win_rate_percent": 55.6, "winning_round_trips": 5, "losing_round_trips": 4,
                "portfolio_value_high": 10_910.00, "portfolio_value_low": 9_702.40, "round_trips": 9,
                "tickers": ["BTC/USD", "ETH/USD"], "start_date": "2022-01-03", "end_date": "2022-12-30",
                "pnl_per_stock": ["BTC/USD": 512.30, "ETH/USD": 129.80],
                "pnl_percent_per_stock": ["BTC/USD": 10.25, "ETH/USD": 2.60],
                "stock_price_change": ["BTC/USD": -64.2, "ETH/USD": -67.5],
                "fees": ["total_fees": 41.22, "total_volume": 16_488.00, "taker_rate": 0.0025, "emulated": true, "venue": "alpaca"],
                "pause_reason_tag": "rate_limited", "pause_provider": "openrouter", "pause_model": "anthropic/claude-sonnet",
                "pause_call_site": "trend_research.pick", "pause_attempts": 5, "pause_bar_time": "2022-06-14T14:00:00",
                "paused_at": "2026-11-12T09:41:00Z", "pause_sample": "429 Too Many Requests: provider is rate limiting this key",
                "total_round_trip_pnl": 642.10, "avg_winning_round_trip": 210.40, "avg_losing_round_trip": -102.55,
                "strategy_schema": opsSchema(),
            ]
        }
        return [
            "id": id, "status": row["status"] ?? "running", "instance_id": "strategy-eb-lab", "strategy_id": "207",
            "pnl": 4_750.71, "pnl_percent": 47.51, "portfolio_start_value": 10_000, "portfolio_end_value": 14_750.71,
            "total_trades": 24, "total_buys": 12, "total_sells": 12, "time_elapsed_seconds": 2_140,
            "win_rate_percent": 58.3, "winning_round_trips": 7, "losing_round_trips": 5,
            "portfolio_value_high": 15_194.10, "portfolio_value_low": 9_841.32, "round_trips": 12,
            "tickers": ["QQQ", "GLD", "XLE"], "start_date": "2024-01-02", "end_date": "2024-12-31",
            "pnl_per_stock": ["QQQ": 842.10, "GLD": 611.37, "XLE": -269.25],
            "pnl_percent_per_stock": ["QQQ": 8.42, "GLD": 6.11, "XLE": -2.69],
            "stock_price_change": ["QQQ": 25.6, "GLD": 27.2, "XLE": 2.4],
            "total_round_trip_pnl": 1_184.22, "avg_winning_round_trip": 312.40, "avg_losing_round_trip": -200.53,
            "strategy_schema": opsSchema(),
            "tr_scorecard": [
                "meta": [
                    "cutoff": "2025-01-31",
                    "notes": ["Engine NAV is price-only; dividends are not reinvested.", "Survivorship: delisted names are absent from the universe."],
                ],
                "overall": ["start": "2024-01-02", "end": "2024-12-31", "cagr": 15.2, "spy_cagr": 23.3, "excess_pp": -8.1,
                            "max_dd": -9.8, "spy_max_dd": -8.4, "pass_all_regimes": false, "pass_plus5": false,
                            "random_pctile_candidates": 41.0, "random_pctile_universe": 63.5,
                            "windows": [
                                ["tag": "bull-24a", "kind": "bull", "start": "2024-01-02", "end": "2024-03-28", "ret": 6.1, "spy_ret": 10.2, "beat": false],
                                ["tag": "chop-24", "kind": "chop", "start": "2024-04-01", "end": "2024-06-28", "ret": 2.4, "spy_ret": 3.9, "beat": false],
                                ["tag": "bear-24", "kind": "bear", "start": "2024-07-15", "end": "2024-08-07", "ret": 1.2, "spy_ret": -5.6, "beat": true],
                            ]],
                "honest": ["untested": true],
                "optimistic": ["start": "2024-01-02", "end": "2024-12-31", "cagr": 15.2, "spy_cagr": 23.3, "excess_pp": -8.1,
                               "max_dd": -9.8, "spy_max_dd": -8.4, "pass_all_regimes": false, "pass_plus5": false, "windows": [Any]()],
            ],
        ]
    }

    private static func opsSchema() -> [String: Any] {
        ["name": "F14 mirror", "strategies": [
            ["strategy": "strategy_f14", "weight": 0.6, "execution_position": 1, "decision_phase": "pre", "execution_scope": "portfolio",
             "conditions": ["sma_window": 200], "config": ["leverage": 1.5, "rebalance": "weekly", "vol_target": 0.18]],
            ["strategy": "graph_nexus_analysis", "weight": 0.4, "execution_position": 2, "decision_phase": "entry", "execution_scope": "symbol",
             "config": ["min_score": 0.35, "lookback_days": 90]],
        ]]
    }

    /// A deterministic walk from `start` to `end` over `count` points.
    private static func opsWalk(_ count: Int, from start: Double, to end: Double, wobble: Double, phase: Double) -> [Double] {
        (0..<count).map { i in
            let t = Double(i) / Double(max(count - 1, 1))
            let base = start + (end - start) * t
            let wave = sin(Double(i) / 3.1 + phase) * wobble + sin(Double(i) / 9.7 + phase * 2) * wobble * 1.6
            return base + wave * sin(t * .pi)
        }
    }

    private static func opsGraphData(_ id: String, now: Date) -> [String: Any] {
        let crypto = id == opsFinishedBacktestId
        let tickers = crypto ? ["BTC/USD", "ETH/USD"] : ["QQQ", "GLD", "XLE"]
        let startText = crypto ? "2022-01-03" : "2024-01-02"
        let start = ISO8601DateFormatter().date(from: "\(startText)T14:30:00Z") ?? now
        let days = 120
        func ts(_ i: Int) -> Double { (start.timeIntervalSince1970 + Double(i) * 3 * 86_400) * 1000 }
        func isoAt(_ i: Int) -> String { ISO8601DateFormatter().string(from: Date(timeIntervalSince1970: ts(i) / 1000)) }
        let portfolio = opsWalk(days, from: 10_000, to: crypto ? 10_642.10 : 14_750.71, wobble: 260, phase: 0.4)
        var prices: [[String: Any]] = []
        let bases: [String: (Double, Double)] = ["QQQ": (402, 505), "GLD": (191, 243), "XLE": (84, 86), "BTC/USD": (46_000, 16_500), "ETH/USD": (3_700, 1_200)]
        for (k, sym) in tickers.enumerated() {
            let (a, b) = bases[sym] ?? (100, 110)
            for (i, close) in opsWalk(days, from: a, to: b, wobble: a * 0.025, phase: Double(k)).enumerated() {
                prices.append(["symbol": sym, "timestamp": isoAt(i), "close": close])
            }
        }
        var trades: [[String: Any]] = []
        var decisions: [[String: Any]] = []
        for (k, sym) in tickers.enumerated() {
            let (a, _) = bases[sym] ?? (100, 110)
            for n in 0..<4 {
                let i = 10 + n * 25 + k * 3
                let buy = n % 2 == 0
                let price = a * (1 + Double(n) * 0.04)
                trades.append([
                    "ticker": sym, "action": buy ? "BUY" : "SELL", "timestamp": isoAt(i), "price": price,
                    "shares": buy ? 4.0 : -4.0, "total": buy ? -price * 4 : price * 4, "cash_after": 6_210.40 + Double(n * 412),
                ])
            }
            for n in 0..<7 {
                let label = n % 3 == 0 ? "1" : (n % 3 == 1 ? "0" : "-1")
                decisions.append([
                    "symbol": sym, "timestamp": isoAt(100 - n * 9), "decision": label,
                    "normalized_score": 0.62 - Double(n) * 0.07, "primary_strategy": "graph_nexus_analysis",
                    "primary_action_intent": "rebalance",
                    "final_reason": "Linked-name momentum above threshold; trend filter on (\(sym) above its 200-day average).",
                    "override_applied": n == 1, "pre_override_action": n == 1 ? "BUY" : "",
                    "strategies": [
                        ["strategy": "strategy_f14", "decision": "1", "weight": 0.6, "action_intent": "hold_core", "reason": "Trend filter on; above the 200-day average."],
                        ["strategy": "graph_nexus_analysis", "decision": label, "weight": 0.4, "reason": "Supplier cluster score 0.41, rising."],
                    ],
                ])
            }
        }
        return [
            "portfolio_value_history": portfolio.enumerated().map { ["timestamp": ts($0.offset), "value": $0.element] },
            "backtest_prices": prices,
            "backtest_trades": trades,
            "backtest_decisions": decisions,
        ]
    }

    private static func opsLlmCost(_ id: String) -> [String: Any] {
        [
            "total_cost_usd": 3.42, "total_calls": 1_284, "ok_calls": 1_279, "failed_calls": 5,
            "total_input_tokens": 4_800_000, "total_output_tokens": 391_200, "total_reasoning_tokens": 88_400,
            "by_model": [["key": "anthropic/claude-sonnet", "cost_usd": 2.91], ["key": "google/gemini-flash", "cost_usd": 0.4821], ["key": "openai/gpt-mini", "cost_usd": 0.0251]],
            "by_call_site": [["key": "trend_research.pick", "cost_usd": 2.11], ["key": "overlay.review", "cost_usd": 1.02], ["key": "nexus.extract", "cost_usd": 0.2872]],
            "by_provider": [["key": "openrouter", "cost_usd": 3.39], ["key": "claude-cli", "cost_usd": 0.0272]],
        ]
    }

    private static func opsLogs(_ id: String) -> [String: Any] {
        ["source": "file", "logs": [
            "2026-11-12 09:30:01 Backtest \(id) started · 3 symbols · 15m bars",
            "2026-11-12 09:30:02 broker: paper portfolio seeded with $10,000.00",
            "2026-11-12 09:30:04 Nexus lookback 1/90 · building event context",
            "2026-11-12 09:31:10 warn: provider slow (2.4 s), retrying",
            "2026-11-12 09:33:47 BUY QQQ 4 @ $402.10 · trend filter on",
            "2026-11-12 09:34:02 skip GDX: below min score 0.35",
            "2026-11-12 09:36:15 SELL XLE 4 @ $86.40 · profit +$12.20",
            "2026-11-12 09:38:41 error: quote missing for XLE, using last close",
            "2026-11-12 09:40:00 Day 41 of 90 · portfolio $14,750.71",
        ]]
    }

    private static func opsPlayback() -> [String: Any] {
        var events: [[String: Any]] = []
        let values = [9_980.0, 9_940.0, 9_921.0, 9_968.0, 10_021.46, 10_004.0, 10_046.0, 10_088.5]
        for (n, value) in values.enumerated() {
            let day = 16 + n
            let label = "Jan \(day), 2024"
            let holdings: [[String: Any]] = [
                ["ticker": "GLD", "qty": 6, "avg": 191.40, "curr": 191.40 + Double(n) * 0.6],
                ["ticker": "QQQ", "qty": 8, "avg": 404.20, "curr": 402.66 + Double(n) * 0.4],
            ]
            events.append(["type": "date", "label": label, "time": "15:45"])
            events.append(["type": "strategy", "name": "strategy_f14", "desc": "Market scan", "reason": "Trend filter on; QQQ above its 200-day average.", "tickers": ["QQQ", "GLD", "XLE"]])
            events.append(["type": "strategy", "name": "graph_nexus_analysis", "desc": "QQQ → BUY", "reason": "Linked-name momentum above threshold."])
            events.append(["type": "outcome", "name": "Weighted score", "details": "QQQ 0.49 · GLD 0.41 · XLE 0.12"])
            events.append(["type": "decision", "buys": [Any](), "sells": [["ticker": "XLE", "qty": 10, "price": 91.89, "reason": "Trend filter off"]]])
            events.append(["type": "portfolio", "value": value, "date": "2024-01-\(day)T15:45:00", "holdings": holdings])
        }
        return ["events": events, "metadata": ["initial_cash": 10_000]]
    }

    // MARK: Strategies

    private static func opsStrategies() -> [[String: Any]] {
        [
            ["id": 207, "name": "F14 mirror", "instances_using": ["f14-paper"], "strategies": [
                ["strategy": "strategy_f14", "weight": 0.6, "execution_position": 0, "decision_phase": "pre", "execution_scope": "portfolio",
                 "config": ["leverage": 1.5, "rebalance": "weekly", "vol_target": 0.18, "sma_window": 200]],
                ["strategy": "graph_nexus_analysis", "weight": 0.3, "execution_position": 1, "decision_phase": "entry", "execution_scope": "symbol",
                 "config": ["min_score": 0.35, "lookback_days": 90]],
                ["strategy": "trailing_stop", "weight": 0.1, "execution_position": 2, "decision_phase": "exit", "execution_scope": "symbol"],
            ]],
            ["id": 200, "name": "Strategy EB — vol-core ETF book with gold and energy sleeves", "instances_using": ["alpaca-main"], "strategies": [
                ["strategy": "strategy_eb", "weight": 1.0], ["strategy": "risk_overlay", "weight": 0.0],
            ]],
            ["id": 210, "name": "IBS close reversal", "instances_using": ["ibs-reversal"], "strategies": [["strategy": "ibs_reversal", "weight": 1.0]]],
            ["id": 194, "name": "Swing trader", "instances_using": ["swing-paper"], "strategies": [["strategy": "swing", "weight": 0.7], ["strategy": "wheel", "weight": 0.3]]],
            ["id": 201, "name": "Nexus lab", "strategies": [["strategy": "graph_nexus_analysis", "weight": 1.0]]],
            ["id": 212, "name": "Sector rotation monthly", "strategies": [["strategy": "rotation", "weight": 1.0]]],
            ["id": 213, "name": "Low-vol quality tilt", "strategies": [Any]()],
        ]
    }

    private static func opsAgentResults(_ now: Date) -> [[String: Any]] {
        func iso(_ daysAgo: Double) -> String { ISO8601DateFormatter().string(from: now.addingTimeInterval(-daysAgo * 86_400)) }
        return [
            ["backtest_id": "166171", "strategy_id": 207, "overall_profit": 4_210.55, "pnl_percent": 42.11, "stocks_used": ["TQQQ", "GLD", "GDX", "XLE", "BIL"], "start_date": "2020-02-18", "end_date": "2020-06-30", "created_at": iso(3)],
            ["backtest_id": "166213", "strategy_id": 207, "overall_profit": 642.10, "pnl_percent": 6.42, "stocks_used": ["BTC/USD", "ETH/USD"], "start_date": "2022-01-03", "end_date": "2022-12-30", "created_at": iso(1)],
            ["backtest_id": "166150", "strategy_id": 207, "overall_profit": -1_022.80, "pnl_percent": -10.23, "stocks_used": ["AMD", "AVGO", "SNDK", "MU"], "start_date": "2025-03-03", "end_date": "2025-09-30", "created_at": iso(6)],
            ["backtest_id": "166122", "strategy_id": 207, "overall_profit": 905.33, "pnl_percent": 9.05, "stocks_used": ["NVDA", "MSFT", "AAPL"], "start_date": "2025-03-03", "end_date": "2025-09-30", "created_at": iso(9)],
            ["backtest_id": "166206", "strategy_id": 200, "overall_profit": 1_204.18, "pnl_percent": 12.04, "stocks_used": ["TQQQ", "GLD"], "start_date": "2025-03-03", "end_date": "2025-09-30", "created_at": iso(2)],
            ["backtest_id": "166199", "strategy_id": 210, "overall_profit": -318.40, "pnl_percent": -3.18, "stocks_used": ["SPY"], "start_date": "2020-02-18", "end_date": "2020-06-30", "created_at": iso(4)],
            ["backtest_id": "166157", "strategy_id": 194, "overall_profit": 2_310.55, "pnl_percent": 23.11, "stocks_used": ["AAPL", "NVDA"], "start_date": "2022-01-03", "end_date": "2022-12-30", "created_at": iso(5)],
        ]
    }

    private static func opsTop5() -> [[String: Any]] {
        [
            ["rank": 1, "strategy_id": 207, "backtest_id": "166171", "overall_profit": 4_210.55, "pnl_percent": 42.11,
             "strategy_snapshot": ["name": "F14 mirror", "strategies": [["strategy": "strategy_f14"], ["strategy": "graph_nexus_analysis"], ["strategy": "trailing_stop"]]]],
            ["rank": 2, "strategy_id": 194, "backtest_id": "166157", "overall_profit": 2_310.55, "pnl_percent": 23.11,
             "strategy_snapshot": ["name": "Swing trader", "strategies": [["strategy": "swing"], ["strategy": "wheel"]]]],
            ["rank": 3, "strategy_id": 200, "backtest_id": "166206", "overall_profit": 1_204.18, "pnl_percent": 12.04,
             "strategy_snapshot": ["name": "Strategy EB", "strategies": [["strategy": "strategy_eb"], ["strategy": "risk_overlay"]]]],
        ]
    }
}
