import Foundation
@testable import IntelliStock

/// Snapshot stub routes for Instances, Backtests, Strategies, Agent runs, Learning, Nexus.
/// Return nil for paths this area doesn't serve; `SnapshotFixtures.respond`
/// then falls through to the shared routes. Use `SnapshotFixtures.json(_:)`
/// for bodies.
///
/// The routes are split by area so they can grow independently:
/// - Instances: this file (`opsInstancesResponse`).
/// - Backtests and Strategies: SnapshotFixtures+OpsBacktests.swift.
/// - Agent runs, Learning and Nexus: SnapshotFixtures+OpsResearch.swift.
nonisolated extension SnapshotFixtures {
    static func opsResponse(parts: [String], query: [String: String], now: Date) -> (status: Int, body: String)? {
        opsInstancesResponse(parts: parts, query: query, now: now)
            ?? opsBacktestsResponse(parts: parts, query: query, now: now)
            ?? opsResearchResponse(parts: parts, query: query, now: now)
    }

    /// The instances the Ops snapshots pin (`PinnedInstancesModel`): one
    /// running on real money, one crashed, one stopped — every card state.
    static let opsPinnedInstanceIds = ["alpaca-main", "f14-paper", "nexus-lab"]

    /// Instances routes: the list (the shared four plus two more, so "Other
    /// instances" has rows of each state), each instance, and an instance's
    /// backtests. Everything else falls through to the shared routes.
    static func opsInstancesResponse(parts: [String], query: [String: String], now: Date) -> (status: Int, body: String)? {
        switch parts.first {
        case "instances":
            if parts.count == 1 { return (200, json(["instances": opsInstances()])) }
            if parts.count == 2, let inst = opsInstances().first(where: { ($0["id"] as? String) == parts[1] }) {
                return (200, json(inst))
            }
            if parts.count == 3, parts[2] == "backtests" {
                return (200, json(opsInstanceBacktests(parts[1], now: now)))
            }
            return nil
        case "backtests":
            // The running backtest's progress poll (only this area's ids).
            if parts.count == 3, parts[2] == "status", parts[1] == "81204" {
                return (200, json(["id": "81204", "status": "running", "progress": 42]))
            }
            return nil
        default:
            return nil
        }
    }

    // MARK: Instances

    private static func opsInstances() -> [[String: Any]] {
        [
            [
                "id": "alpaca-main", "name": "Strategy EB", "created_by": "user", "run_command": true, "crashed": false,
                "strategy_id": "200", "strategy": ["id": "200", "name": "Strategy EB"],
                "brokerage_id": alpacaLiveId,
                "brokerage": ["id": alpacaLiveId, "account_name": "Alpaca Live", "brokerage_type": "alpaca", "alpaca_paper": false],
                "alpaca_data_brokerage_id": alpacaLiveId,
                "granularity_time_increment": 86_400, "uptime_seconds": 22_320, "max_usage": 6_000,
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
                "granularity_time_increment": 900,
                "stocks": [Any](),
            ],
            [
                "id": "ibs-reversal", "name": "IBS close reversal", "created_by": "user", "run_command": true, "crashed": false,
                "strategy_id": "210", "strategy": ["id": "210", "name": "IBS reversal"],
                "brokerage_id": paperTestId,
                "brokerage": ["id": paperTestId, "account_name": "Paper Test", "brokerage_type": "alpaca", "alpaca_paper": true],
                "granularity_time_increment": 3_600, "uptime_seconds": 9_120,
                "stocks": ["SPY"],
            ],
            [
                "id": "pit-capture", "name": "pit-capture", "created_by": "ai", "run_command": false, "crashed": false,
                "granularity_time_increment": 60,
                "stocks": ["SPY", "QQQ"],
            ],
        ]
    }

    // MARK: An instance's backtests

    private static func opsInstanceBacktests(_ instanceId: String, now: Date) -> [String: Any] {
        func iso(_ daysAgo: Double) -> String {
            ISO8601DateFormatter().string(from: now.addingTimeInterval(-daysAgo * 86_400))
        }
        let rows: [[String: Any]] = [
            ["id": "81204", "stocks": ["TQQQ", "GLD", "GDX", "XLE", "BIL", "SPY"], "start_date": "2026-06-01", "end_date": "2026-09-01",
             "status": "running", "progress": 42, "instance_id": instanceId, "granularity": "900"],
            ["id": "81187", "stocks": ["TQQQ", "GLD"], "start_date": "2025-01-01", "end_date": "2025-12-31",
             "status": "completed", "completed_at": iso(2), "pnl": 18_432.10, "pnl_percent": 18.43,
             "time_elapsed_seconds": 2_412, "instance_id": instanceId, "granularity": "900"],
            ["id": "81150", "stocks": ["XLE", "GLD"], "start_date": "2024-03-01", "end_date": "2024-09-01",
             "status": "completed", "completed_at": iso(9), "pnl": -2_104.55, "pnl_percent": -2.10,
             "time_elapsed_seconds": 1_380, "instance_id": instanceId, "granularity": "900"],
            ["id": "81122", "stocks": ["GDX"], "start_date": "2024-01-01", "end_date": "2024-06-30",
             "status": "failed", "instance_id": instanceId, "granularity": "900"],
            ["id": "81090", "stocks": ["TQQQ", "BIL"], "start_date": "2023-06-01", "end_date": "2023-12-31",
             "status": "completed", "completed_at": iso(31), "pnl": 7_810.00, "pnl_percent": 7.81,
             "time_elapsed_seconds": 1_960, "instance_id": instanceId, "granularity": "900"],
        ]
        return ["backtests": rows, "total": 37, "total_pages": 3, "page": 1]
    }
}
