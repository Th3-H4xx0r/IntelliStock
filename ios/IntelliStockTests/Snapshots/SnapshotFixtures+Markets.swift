import Foundation
@testable import IntelliStock

/// Snapshot stub routes for Kalshi, Crypto, Brokerages, Models, Token usage, Settings.
/// Return nil for paths this area doesn't serve; `SnapshotFixtures.respond`
/// then falls through to the shared routes. Use `SnapshotFixtures.json(_:)`
/// for bodies.
///
/// - `marketsResponse` serves the paths only these screens call (the Kalshi
///   instance, edge and backtest routes, `/instances/:id/kalshi/*`,
///   `/kalshi/backtests/*`, `/llm-usage/*`), for every snapshot.
/// - `marketsOverride` replaces shared routes with richer data (the
///   brokerage list, saved models, push devices, notification preferences,
///   crypto instances). Only `SnapshotScreensMarkets` applies it, through
///   `marketsServices()`, so other areas' screenshots keep the shared data.
nonisolated extension SnapshotFixtures {
    static let kalshiInstanceId = "kalshi-soccer"
    static let kalshiBacktestId = "kbt7f3a91c2e4"
    static let cryptoInstanceId = "crypto-main"

    static func marketsResponse(parts: [String], query: [String: String], now: Date) -> (status: Int, body: String)? {
        switch parts.first {
        case "brokerages":
            // /brokerages/<kalshi>/kalshi/{instances,edges,backtests}
            guard parts.count == 4, parts[1] == kalshiId, parts[2] == "kalshi" else { return nil }
            switch parts[3] {
            case "instances": return (200, json(["instances": mkKalshiInstances()]))
            case "edges": return (200, json(["edges": mkKalshiEdges()]))
            case "backtests": return (200, json(["backtests": mkKalshiBacktests()]))
            default: return nil
            }
        case "instances":
            // /instances/<kalshi instance>/kalshi/{detail,decisions,live,orders}
            guard parts.count == 4, parts[1] == kalshiInstanceId, parts[2] == "kalshi" else { return nil }
            switch parts[3] {
            case "detail": return (200, json(mkKalshiDetail()))
            case "decisions": return (200, json(mkKalshiDecisions(now)))
            case "live": return (200, json(mkKalshiLive()))
            case "orders": return (200, json(mkKalshiOrders()))
            default: return nil
            }
        case "kalshi":
            // /kalshi/backtests/<id>/{status,results}
            guard parts.count == 4, parts[1] == "backtests" else { return nil }
            return (200, json(mkKalshiBacktestResult(now)))
        case "llm-usage":
            guard parts.count == 2 else { return nil }
            switch parts[1] {
            case "summary": return (200, json(mkUsageSummary()))
            case "timeseries": return (200, json(mkUsageTimeseries(now)))
            case "top-spenders": return (200, json(query["group_by"] == "call_site" ? mkTopByCallSite() : mkTopByModel()))
            case "by-backtest": return (200, json(mkByBacktest(now)))
            case "calls": return (200, json(mkRecentCalls(now)))
            default: return nil
            }
        default:
            return nil
        }
    }

    /// The markets screens' richer versions of shared routes.
    static func marketsOverride(_ request: URLRequest) -> (status: Int, body: String)? {
        guard request.method.uppercased() == "GET" else { return nil }
        let parts = request.path.split(separator: "/").map(String.init)
        let now = Date()
        switch parts.first {
        case "brokerages" where parts.count == 1:
            return (200, json(["accounts": mkBrokerages(now)]))
        case "models" where parts.count == 1:
            return (200, json(["models": mkModels()]))
        case "push" where parts.count == 2 && parts[1] == "devices":
            return (200, json(["devices": mkPushDevices()]))
        case "notification-preferences":
            return (200, json(mkNotificationPrefs()))
        case "instances":
            if parts.count == 1 { return (200, json(["instances": mkCryptoInstances()])) }
            if parts.count == 2, let inst = mkCryptoInstances().first(where: { ($0["id"] as? String) == parts[1] }) {
                return (200, json(inst))
            }
            if parts.count == 3, parts[2] == "backtests" {
                return (200, json(["backtests": mkCryptoBacktests(), "total": 3]))
            }
            return nil
        case "strategies":
            return (200, json(["strategies": [["id": "crypto-adaptive", "name": "Adaptive", "kind": "crypto"]]]))
        default:
            return nil
        }
    }

    // MARK: Helpers

    private static func mkISO(_ date: Date) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f.string(from: date)
    }

    // MARK: Brokerages

    private static func mkBrokerages(_ now: Date) -> [[String: Any]] {
        [
            ["id": alpacaLiveId, "account_name": "Alpaca Live", "brokerage_type": "alpaca", "status": "active",
             "alpaca_paper": false, "alpaca_account_number": "940219876", "last_refresh_at": mkISO(now.addingTimeInterval(-420))],
            ["id": paperTestId, "account_name": "Paper Test", "brokerage_type": "alpaca", "status": "active",
             "alpaca_paper": true, "alpaca_account_number": "PA3IBY5S84PG", "last_refresh_at": mkISO(now.addingTimeInterval(-600))],
            ["id": swingPaperId, "account_name": "Swing Trade Paper", "brokerage_type": "alpaca", "status": "expired",
             "alpaca_paper": true, "alpaca_account_number": "PA37JPXGU196", "last_refresh_at": mkISO(now.addingTimeInterval(-86_400 * 3)),
             "last_error": "401 Unauthorized: the API key was revoked"],
            ["id": kalshiId, "account_name": "Kalshi Live", "brokerage_type": "kalshi", "status": "active", "kalshi_environment": "live"],
            ["id": "acct-binance", "account_name": "Binance.US Paper", "brokerage_type": "binanceus", "status": "pending", "alpaca_paper": true],
        ]
    }

    // MARK: Models

    private static func mkModels() -> [[String: Any]] {
        [
            ["id": "m-gemini", "name": "Gemini Flash — Main", "provider": "gemini", "model": "gemini-3-flash-preview",
             "api_key": "••••6f2a", "reasoning_effort": "low", "created_at": "2026-09-14T10:02:00Z"],
            ["id": "m-claude", "name": "Claude Sonnet (subscription)", "provider": "claude-cli", "model": "claude-sonnet-4-6",
             "cli_path": "claude", "created_at": "2026-10-02T16:40:00Z"],
            ["id": "m-openrouter", "name": "OpenRouter — DeepSeek V3.2 (high effort)", "provider": "openrouter",
             "model": "deepseek/deepseek-v3.2", "api_key": "••••91cd", "reasoning_effort": "high", "created_at": "2026-10-21T08:15:00Z"],
            ["id": "m-ollama", "name": "Local Qwen 3", "provider": "ollama", "model": "qwen3:32b",
             "ollama_base_url": "http://localhost:11434", "ollama_think": "off", "created_at": "2026-11-03T19:22:00Z"],
        ]
    }

    // MARK: Settings

    private static func mkPushDevices() -> [[String: Any]] {
        [["device_token": "a4f09c1e77d2b3e8c19f0e6d4b2a1c7e9f80d3b5", "platform": "ios", "env": "production",
          "app_version": "1.4.0", "last_seen": "2026-11-12T08:31:00Z"]]
    }

    private static func mkNotificationPrefs() -> [String: Any] {
        let types: [[String: Any]] = [
            ["key": "order_fill", "group": "Trading", "label": "Order fills", "desc": "A buy or sell order filled at the broker"],
            ["key": "order_rejected", "group": "Trading", "label": "Order rejections", "desc": "The broker refused an order"],
            ["key": "instance_crash", "group": "Instances", "label": "Instance crashes", "desc": "An instance stopped unexpectedly"],
            ["key": "daily_digest", "group": "Digests", "label": "Daily digest", "desc": "Morning and evening market summaries"],
        ]
        return [
            "categories": [
                "order_fill": ["discord": true, "push": true],
                "order_rejected": ["discord": true, "push": false],
                "instance_crash": ["discord": true, "push": true],
                "daily_digest": ["discord": true, "push": false],
            ],
            "types": types,
        ]
    }

    // MARK: Crypto

    private static func mkCryptoInstances() -> [[String: Any]] {
        [
            [
                "id": cryptoInstanceId, "name": "Crypto — core allocation", "kind": "crypto", "created_by": "user",
                "run_command": true, "crashed": false, "uptime_seconds": 186_400,
                "brokerage_id": paperTestId, "strategy_id": "crypto-adaptive",
                "stocks": ["BTC/USD", "ETH/USD", "SOL/USD"],
                "crypto_config": [
                    "band": "medium", "strategy": "adaptive",
                    "allocations": [["symbol": "BTC/USD", "pct": 0.4], ["symbol": "ETH/USD", "pct": 0.2], ["symbol": "SOL/USD", "pct": 0.1]],
                ],
            ],
            [
                "id": "crypto-dynamic", "name": "Mean reversion — dynamic", "kind": "crypto", "created_by": "ai",
                "run_command": false, "crashed": false, "brokerage_id": paperTestId, "strategy_id": "crypto-meanrev",
                "stocks": [String](),
                "crypto_config": ["band": "high", "strategy": "meanrev", "allocations": [Any]()],
            ],
        ]
    }

    private static func mkCryptoBacktests() -> [[String: Any]] {
        [
            ["id": "bt812440", "status": "running", "stocks": ["BTC/USD", "ETH/USD", "SOL/USD"], "start_date": "2025-11-01",
             "end_date": "2026-11-01", "pnl": NSNull(), "pnl_percent": NSNull()],
            ["id": "bt811902", "status": "finished", "stocks": ["BTC/USD", "ETH/USD", "SOL/USD"], "start_date": "2024-11-01",
             "end_date": "2025-11-01", "pnl": 1_284.5, "pnl_percent": 12.85],
            ["id": "bt809117", "status": "finished", "stocks": [String](), "start_date": "2023-10-01",
             "end_date": "2024-03-01", "pnl": -412.0, "pnl_percent": -4.12],
        ]
    }

    // MARK: Kalshi

    private static func mkKalshiInstances() -> [[String: Any]] {
        [
            ["id": kalshiInstanceId, "name": "Soccer edge — live", "running": true, "live_enabled": true],
            ["id": "kalshi-paper", "name": "Lower divisions (paper)", "running": false, "live_enabled": false],
        ]
    }

    private static func mkKalshiEdges() -> [[String: Any]] {
        [
            ["market_ticker": "KXSERIEBGAME-26NOV14PALBAR-PAL", "side": "yes", "edge": 0.071],
            ["market_ticker": "KXLIGUE2GAME-26NOV15CAEPAU-DRAW", "side": "yes", "edge": 0.054],
            ["market_ticker": "KXEPLGAME-26NOV15ARSCHE-ARS", "side": "yes", "edge": 0.042],
        ]
    }

    private static func mkKalshiDetail() -> [String: Any] {
        [
            "id": kalshiInstanceId, "name": "Soccer edge — live", "running": true, "brokerage_id": kalshiId,
            "environment": "live", "live_enabled": true,
            "config": [
                "edge_threshold": 0.04, "kelly_fraction": 0.125, "max_contracts": 50, "leagues": ["EPL", "Serie B", "Ligue 2"],
                "paper_mode": true, "poll_seconds": 60,
            ],
        ]
    }

    private static func mkKalshiDecisions(_ now: Date) -> [String: Any] {
        let kickoff = now.timeIntervalSince1970 + 26 * 3_600 + 14 * 60
        let later = now.timeIntervalSince1970 + 2 * 86_400 + 5 * 3_600
        func side(_ fixture: String, _ match: String, _ home: String, _ away: String, _ s: String, _ pick: String,
                  fair: Double, edge: Double, decision: String, sharp: Double?, ko: Double, history: [Double]) -> [String: Any] {
            [
                "fixture_id": fixture, "match": match, "home": home, "away": away, "side": s, "pick_label": pick, "pick_logo": "",
                "fused_fair": fair, "edge": edge, "decision": decision, "sharp_prob": sharp ?? NSNull(), "kickoff_ts": ko,
                "home_elo": 1712, "away_elo": 1648, "home_xg": 1.42, "away_xg": 1.08,
                "ts": mkISO(now.addingTimeInterval(-540)), "edge_history": history.map { ["edge": $0] },
                "model_prob": fair - 0.01, "llm_adjustment": 0.01, "size": decision == "placed" ? 12 : 0, "paper": true,
                "llm_rationale": decision == "placed" ? "Palermo's first-choice striker is back from injury and Bari have lost three of four away; the books lag the line move." : "",
                "block_reason": decision == "blocked" ? "Per-league cap reached for Serie B" : (decision == "skipped" ? "Edge below the 4% threshold" : ""),
                "entry_edge": decision == "placed" ? edge + 0.006 : NSNull(),
            ]
        }
        let rows: [[String: Any]] = [
            side("f1", "Palermo vs Bari", "Palermo", "Bari", "home", "Palermo to win", fair: 0.52, edge: 0.071, decision: "placed", sharp: 0.51, ko: kickoff, history: [0.03, 0.045, 0.052, 0.066, 0.071]),
            side("f1", "Palermo vs Bari", "Palermo", "Bari", "draw", "Draw", fair: 0.27, edge: -0.012, decision: "skipped", sharp: 0.28, ko: kickoff, history: [0.01, 0.0, -0.01, -0.012]),
            side("f1", "Palermo vs Bari", "Palermo", "Bari", "away", "Bari to win", fair: 0.21, edge: -0.034, decision: "skipped", sharp: nil, ko: kickoff, history: [-0.02, -0.03, -0.034]),
            side("f2", "Caen vs Pau", "Caen", "Pau", "draw", "Draw", fair: 0.31, edge: 0.054, decision: "blocked", sharp: 0.30, ko: later, history: [0.02, 0.04, 0.054]),
            side("f2", "Caen vs Pau", "Caen", "Pau", "home", "Caen to win", fair: 0.44, edge: 0.018, decision: "queued", sharp: 0.43, ko: later, history: [0.01, 0.018]),
        ]
        return [
            "decisions": rows,
            "summary": ["placed": 14, "skipped": 212, "queued": 3, "blocked": 2,
                        "realized_pnl_cents": 1_840, "unrealized_pnl_cents": -260, "open_positions": 4],
        ]
    }

    private static func mkKalshiLive() -> [String: Any] {
        ["matches": [[
            "id": "live1", "home": "Arsenal", "away": "Chelsea", "home_logo": "", "away_logo": "",
            "score": ["home": 1, "away": 0, "clock": "63'"],
            "market_probs": ["home": 0.68, "draw": 0.22, "away": 0.10],
            "news": "Chelsea down to ten men after a second yellow on 58'.",
            "decisions": [["action": "add", "size": 6], ["action": "hold", "size": 0]],
        ]]]
    }

    private static func mkKalshiOrders() -> [String: Any] {
        [
            "placed": [["match": "Palermo vs Bari", "pick_label": "Palermo to win", "pick_logo": "", "contracts": 12, "edge": 0.071, "in_play": false]],
            "fills": [["match": "Arsenal vs Chelsea", "pick_label": "Arsenal to win", "pick_logo": "", "contracts": 25, "price_cents": 59, "action": "buy"]],
            "mock": [["match": "Sampdoria vs Bari", "pick_label": "Sampdoria to win", "pick_logo": "", "contracts": 20,
                      "entry_cents": 41, "mark_cents": 47, "unrealized_pnl_cents": 120]],
            "mock_history": [["match": "Lorient vs Metz", "pick_label": "Draw", "pick_logo": "", "contracts": 15,
                              "price_cents": 29, "realized_pnl_cents": 1_065, "outcome": "win"]],
        ]
    }

    private static func mkKalshiBacktests() -> [[String: Any]] {
        [
            ["id": kalshiBacktestId, "status": "finished", "start_date": "2026-08-01", "end_date": "2026-10-31",
             "progress": 100, "summary": ["pnl_cents": 4_212]],
            ["id": "kbt5512ab90ff", "status": "running", "start_date": "2026-05-01", "end_date": "2026-07-31",
             "progress": 42, "summary": ["pnl_cents": NSNull()]],
            ["id": "kbt30c1d7e655", "status": "error", "start_date": "2026-02-01", "end_date": "2026-04-30",
             "progress": 0, "summary": ["pnl_cents": -910]],
        ]
    }

    private static func mkKalshiBacktestResult(_ now: Date) -> [String: Any] {
        let base = (now.timeIntervalSince1970 - 40 * 86_400).rounded()
        let pnls: [Double] = [420, -300, 610, 180, -250, 540, 330, -120, 700, 260, -380, 900, 210, 420, 192]
        var equity: [Double] = []
        var running = 0.0
        var trades: [[String: Any]] = []
        let teams = [("Palermo", "Bari"), ("Caen", "Pau"), ("Lorient", "Metz"), ("Cremonese", "Spezia"), ("Grenoble", "Annecy")]
        for (i, p) in pnls.enumerated() {
            running += p
            equity.append(running)
            let t = teams[i % teams.count]
            trades.append([
                "home": t.0, "away": t.1, "league": i % 2 == 0 ? "Serie B" : "Ligue 2", "side": i % 3 == 0 ? "draw" : "home",
                "pick_label": i % 3 == 0 ? "Draw" : "\(t.0) to win", "entry_cents": 38 + i, "size": 10 + i % 5,
                "edge": 0.04 + Double(i % 4) * 0.01, "sharp_prob": i % 4 == 0 ? NSNull() : 0.45,
                "realized_pnl_cents": p, "outcome": p >= 0 ? "win" : "loss", "kickoff": base + Double(i) * 2.6 * 86_400,
            ])
        }
        return [
            "status": "finished",
            "summary": [
                "pnl_cents": running, "roi": 0.084, "n_bets": pnls.count, "win_rate": 0.67, "clv_avg": 0.021,
                "api_calls": 212, "cache_hits": 1_840, "n_fixtures": 318, "bet": 15, "no_bet": 281, "unsettled": 2,
                "unmatched": 9, "no_candle_data": 11, "profit_confidence": 0.81,
                "pnl_ci_low_cents": -640, "pnl_ci_high_cents": 7_920,
            ],
            "result": [
                "trades": trades,
                "equity_curve": equity,
                "decision_log": [
                    ["label": "Palermo vs Bari · Palermo", "reason": "Edge 7.1% over the 4% threshold", "decision": "placed"],
                    ["label": "Caen vs Pau · Draw", "reason": "No sharp line and edge below the 5% bar", "decision": "no_bet"],
                ],
                "logs": ["[backtest] 318 fixtures loaded", "[backtest] 15 bets placed, 2 unsettled"],
            ],
        ]
    }

    // MARK: Token usage

    private static func mkUsageSummary() -> [String: Any] {
        [
            "total_cost_usd": 18.4231, "total_tokens": 9_412_880, "total_calls": 1_284, "max_plan_estimate_usd": 41.20,
            "by_provider": [
                ["provider": "openrouter", "cost_usd": 11.902],
                ["provider": "gemini", "cost_usd": 4.311],
                ["provider": "claude-cli", "cost_usd": 2.21],
            ],
            "telemetry_health": ["buffer_depth": 3, "last_flush_age_s": 6, "write_errors_24h": 0],
        ]
    }

    private static func mkUsageTimeseries(_ now: Date) -> [String: Any] {
        let start = (now.timeIntervalSince1970 / 3_600).rounded(.down) * 3_600 - 23 * 3_600
        var rows: [[String: Any]] = []
        for h in 0..<24 {
            let ts = Int((start + Double(h) * 3_600) * 1_000)
            let wave = 0.5 + 0.5 * sin(Double(h) / 3.0)
            rows.append(["provider": "openrouter", "bucket_start_ts": ts, "cost_usd": 0.25 + 0.5 * wave, "tokens": 180_000, "calls": 30])
            rows.append(["provider": "gemini", "bucket_start_ts": ts, "cost_usd": 0.08 + 0.2 * (1 - wave), "tokens": 90_000, "calls": 18])
            if h % 3 == 0 {
                rows.append(["provider": "claude-cli", "bucket_start_ts": ts, "cost_usd": 0.27, "tokens": 40_000, "calls": 4])
            }
        }
        return ["rows": rows]
    }

    private static func mkTopByModel() -> [[String: Any]] {
        [
            ["key": "deepseek/deepseek-v3.2", "calls": 612, "tokens": 5_120_000, "cost_usd": 11.902],
            ["key": "gemini-3-flash-preview", "calls": 588, "tokens": 3_480_000, "cost_usd": 4.311],
            ["key": "claude-sonnet-4-6", "calls": 84, "tokens": 812_880, "cost_usd": 2.21],
        ]
    }

    private static func mkTopByCallSite() -> [[String: Any]] {
        [
            ["key": "graph_nexus_analysis.score", "calls": 702, "tokens": 6_010_000, "cost_usd": 12.40],
            ["key": "kalshi.analyst", "calls": 410, "tokens": 2_100_000, "cost_usd": 4.02],
            ["key": "chatbot.reply", "calls": 172, "tokens": 1_302_880, "cost_usd": 2.0031],
        ]
    }

    private static func mkByBacktest(_ now: Date) -> [[String: Any]] {
        [
            ["kind": "backtest", "backtest_id": "812440", "display_label": "Crypto — core allocation", "instance_id": cryptoInstanceId,
             "first_ts": Int(now.addingTimeInterval(-5 * 3_600).timeIntervalSince1970 * 1_000), "calls": 402, "ok_calls": 402,
             "failed_calls": 0, "tokens": 2_910_000, "cost_usd": 6.12],
            ["kind": "live", "instance_id": "alpaca-main", "display_label": "Strategy EB",
             "first_ts": Int(now.addingTimeInterval(-20 * 3_600).timeIntervalSince1970 * 1_000), "calls": 288, "ok_calls": 281,
             "failed_calls": 7, "tokens": 1_840_000, "cost_usd": 3.88],
        ]
    }

    private static func mkRecentCalls(_ now: Date) -> [[String: Any]] {
        let ms = { (seconds: Double) in Int(now.addingTimeInterval(-seconds).timeIntervalSince1970 * 1_000) }
        return [
            ["id": "c1", "ts": ms(40), "provider": "openrouter", "model": "deepseek/deepseek-v3.2", "input_tokens": 8_412,
             "output_tokens": 611, "total_cost_usd": 0.0041, "strategy": "strategy_eb", "call_site": "graph_nexus_analysis.score"],
            ["id": "c2", "ts": ms(180), "provider": "gemini", "model": "gemini-3-flash-preview", "input_tokens": 3_120,
             "output_tokens": 402, "total_cost_usd": 0.0009, "strategy": "kalshi", "call_site": "kalshi.analyst"],
            ["id": "c3", "ts": ms(900), "provider": "claude-cli", "model": "claude-sonnet-4-6", "input_tokens": 12_004,
             "output_tokens": 1_208, "total_cost_usd": 0.0, "strategy": "chatbot", "call_site": "chatbot.reply"],
        ]
    }
}
