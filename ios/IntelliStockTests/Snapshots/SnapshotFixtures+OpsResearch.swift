import Foundation
@testable import IntelliStock

/// Snapshot stub routes for Agent runs, Learning and Nexus (part of the Ops
/// area, chained from `opsResponse` in SnapshotFixtures+Ops.swift). Return
/// nil for paths this area doesn't serve.
///
/// The shared routes already answer `agent/control` (stopped) and
/// `nexus/status` (a bare build) for the Dashboard, so they are not
/// overridden here; `researchOverride(_:agentRunning:nexus:)` gives a
/// screen's own stub a running agent or a fuller Nexus status instead.
nonisolated extension SnapshotFixtures {
    static func opsResearchResponse(parts: [String], query: [String: String], now: Date) -> (status: Int, body: String)? {
        switch parts.first {
        case "agent":
            if parts.count == 2, parts[1] == "runs" { return (200, json(researchAgentRuns(now))) }
        case "learning":
            guard parts.count == 2 else { return nil }
            switch parts[1] {
            case "overview": return (200, json(researchLearningOverview()))
            case "findings": return (200, json(["findings": researchLearningFindings(now)]))
            case "funnels": return (200, json(["funnels": researchLearningFunnels()]))
            case "approvals": return (200, json(["pending": researchLearningApprovals(now)]))
            case "noise-floors": return (200, json(["floors": researchLearningFloors()]))
            case "control": return (200, json(["running": true, "config": ["mode": "observe"]]))
            case "targets": return (200, json(researchLearningTargets()))
            default: return nil
            }
        case "nexus-graph-builds":
            return (200, json(["lines": [Any](), "total_lines": 0, "status": "none"]))
        default:
            break
        }
        return nil
    }

    /// Nexus status variants for a screen's own stub.
    enum ResearchNexus {
        case built, building
    }

    /// Routes a screen's own stub answers before the shared ones: the agent
    /// running (`agentRunning`), and a fuller Nexus status (`nexus`).
    static func researchOverride(_ request: URLRequest, agentRunning: Bool = false, nexus: ResearchNexus? = nil) -> (status: Int, body: String)? {
        let parts = request.path.split(separator: "/").map(String.init)
        if agentRunning, parts == ["agent", "control"], request.method.uppercased() == "GET" {
            return (200, json(["running": true, "paused": false, "count_today": 6]))
        }
        if let nexus, parts == ["nexus", "status"] {
            return (200, json(researchNexusStatus(nexus, now: Date())))
        }
        if nexus != nil, parts == ["nexus", "cache"] {
            return (200, json(["available": true, "cache_root": "/data/nexus-cache", "entries": [
                ["path": "sec_filings", "is_dir": true],
                ["path": "13f_holdings", "is_dir": true],
                ["path": "universe.json", "is_dir": false, "size_bytes": 48_210],
            ]]))
        }
        return nil
    }

    // MARK: Agent runs

    private static func researchAgentRuns(_ now: Date) -> [String: Any] {
        func iso(_ minutesAgo: Double) -> String {
            ISO8601DateFormatter().string(from: now.addingTimeInterval(-minutesAgo * 60))
        }
        let generate: [String: Any] = ["label": "Generate strategy", "status": "passed", "details": "3 sub-strategies, 2 LLM stages"]
        let runs: [[String: Any]] = [
            ["id": "r1", "cycle_id": "c2", "name": "Gold trend with vol cap", "status": "running", "created_at": iso(4),
             "stages": [
                generate,
                ["label": "Quick backtest · 2024", "status": "passed", "stocks": ["QQQ", "GLD", "XLE"], "pnl": 412.5, "pnl_pct": 4.12, "details": "Beat SPY by 1.2 pts"],
                ["label": "Full backtest · 2020–2024", "status": "running", "stocks": ["QQQ", "GLD", "XLE", "TLT"]],
             ]],
            ["id": "r2", "cycle_id": "c2", "name": "IBS reversal on QQQ", "status": "passed", "created_at": iso(8),
             "final_result": "Saved as strategy #212",
             "stages": [
                generate,
                ["label": "Quick backtest · 2024", "status": "passed", "stocks": ["QQQ", "GLD", "XLE"], "pnl": 232.5, "pnl_pct": 2.32, "details": "Beat SPY by 1.2 pts"],
                ["label": "Full backtest · 2020–2024", "status": "passed", "stocks": ["QQQ", "GLD", "XLE", "TLT"], "pnl": 1820.0, "pnl_pct": 18.2, "details": "Bear window 2022: −3.1%"],
             ]],
            ["id": "r3", "cycle_id": "c2", "name": "Linked-name momentum", "status": "failed", "created_at": iso(12),
             "final_result": "Lost to SPY in the 2022 bear window",
             "stages": [
                generate,
                ["label": "Quick backtest · 2024", "status": "passed", "stocks": ["QQQ", "GLD", "XLE"], "pnl": 52.5, "pnl_pct": 0.52, "details": "Beat SPY by 1.2 pts"],
                ["label": "Full backtest · 2020–2024", "status": "failed", "stocks": ["QQQ", "GLD", "XLE", "TLT"], "pnl": -640.2, "pnl_pct": -6.4, "details": "Bear window 2022: −3.1%"],
             ]],
            ["id": "r4", "cycle_id": "c1", "name": "Sector rotation monthly", "status": "tossed", "created_at": iso(136),
             "final_result": "Trailed SPY in the quick backtest",
             "stages": [
                generate,
                ["label": "Quick backtest · 2024", "status": "failed", "stocks": ["QQQ", "GLD", "XLE"], "pnl": -127.5, "pnl_pct": -1.28, "details": "Trailed SPY by 6.4 pts"],
             ]],
            ["id": "r5", "cycle_id": "c1", "name": "TQQQ core, GLD hedge", "status": "duplicate", "created_at": iso(140),
             "stages": [["label": "Generate strategy", "status": "passed", "details": "Matches strategy #204"]]],
            ["id": "r6", "cycle_id": "c1", "name": "Low-vol quality tilt", "status": "running", "created_at": iso(144),
             "stages": [Any]()],
        ]
        return ["runs": runs, "total": 46, "total_pages": 8, "page": 1]
    }

    // MARK: Learning

    private static func researchLearningOverview() -> [String: Any] {
        ["mode": "observe", "acts_autonomously": false, "enabled": true, "open_findings": 3,
         "runs_observed": 128, "decisions_observed": 4_210, "refusals_observed": 312, "engine_running": true]
    }

    private static func researchLearningFindings(_ now: Date) -> [[String: Any]] {
        let iso = ISO8601DateFormatter().string(from: now.addingTimeInterval(-3 * 3_600))
        return [
            ["id": "f1", "kind": "funnel_leak", "target": "alpaca-main", "severity": "high",
             "title": "Buys refused at the funding gate on 9 of 12 rebalance days",
             "detail": "The queue gate held every buy behind an unsettled sell; the book sat 38% in cash for most of the window.",
             "detected_at": iso, "run_id": "bt-81187", "status": "open",
             "evidence": ["refused_buys": 41, "window": "2025-01-01 → 2025-12-31", "cash_pct_mean": 0.38]],
            ["id": "f2", "kind": "turnover", "target": "swing-paper", "severity": "medium",
             "title": "Turnover 290% a month, above the 50% break-even",
             "detail": "Most of the churn is full-book rebalances into the open.",
             "detected_at": iso, "run_id": "bt-81150", "status": "open", "evidence": ["turnover_pct": 290]],
            ["id": "f3", "kind": "noise", "target": "f14-paper", "severity": "low",
             "title": "Two runs of one window differ by 4.1pp",
             "detail": "Below the 16pp measured elsewhere; still above this target's floor.",
             "detected_at": iso, "run_id": "bt-81090", "status": "open", "evidence": [String: Any]()],
        ]
    }

    private static func researchLearningFunnels() -> [[String: Any]] {
        [
            ["run_id": "bt-81187", "target": "alpaca-main", "decided": 312, "executed": 140, "refused": 172, "buy_decided": 160, "buy_executed": 31],
            ["run_id": "bt-81150", "target": "swing-paper", "decided": 98, "executed": 81, "refused": 17, "buy_decided": 50, "buy_executed": 44],
        ]
    }

    private static func researchLearningApprovals(_ now: Date) -> [[String: Any]] {
        let iso = ISO8601DateFormatter().string(from: now.addingTimeInterval(-40 * 60))
        return [
            ["id": "a1", "rung": "Live (capped)", "action_class": "parameter_change", "target": "alpaca-main",
             "summary": "Lower the bull peak-banking threshold from 28% to 18%", "document_id": "200",
             "requested_at": iso, "holds_forever": true],
            ["id": "a2", "rung": "Paper", "action_class": "lever_toggle", "target": "f14-paper",
             "summary": "Turn on the breadth filter for the paper mirror", "document_id": "207",
             "requested_at": iso, "holds_forever": false],
        ]
    }

    private static func researchLearningFloors() -> [[String: Any]] {
        [
            ["target": "alpaca-main", "window_class": "3-month bull", "floor_pp": 0.52, "n": 6, "measured": true, "reason": ""],
            ["target": "swing-paper", "window_class": "6-month chop", "floor_pp": 0, "n": 1, "measured": false, "reason": "needs 3 repeats"],
        ]
    }

    private static func researchLearningTargets() -> [String: Any] {
        [
            "strategies": [
                ["id": "200", "name": "Strategy EB", "sub_strategies": 3, "instance_names": ["Strategy EB"], "money": "live"],
                ["id": "207", "name": "Strategy F14", "sub_strategies": 1, "instance_names": ["F14 mirror"], "money": "paper"],
                ["id": "201", "name": "Lab doc", "sub_strategies": 2, "instance_names": [Any](), "money": "unknown"],
            ],
            "instances": [
                ["id": "alpaca-main", "name": "Strategy EB", "kind": "equity", "strategy_id": "200", "running": true, "money": "live"],
                ["id": "f14-paper", "name": "F14 mirror", "kind": "equity", "strategy_id": "207", "running": true, "money": "paper"],
                ["id": "nexus-lab", "name": "Nexus lab", "kind": "equity", "strategy_id": "201", "running": false, "money": "none"],
            ],
            "document_allowlist": ["207"],
            "watched_instances": [Any](),
            "watching_all": true,
        ]
    }

    // MARK: Nexus

    private static func researchNexusStatus(_ variant: ResearchNexus, now: Date) -> [String: Any] {
        let iso = ISO8601DateFormatter()
        let labels = [
            "Phase 1: S&P 500 universe", "Phase 2: SEC filings index", "Phase 2b: SEC sector/industry",
            "Phase 3: Supplier links", "Phase 4: Customer links", "Phase 5: Competitor links",
            "Phase 6: Partnerships", "Phase 7: 13F holdings", "Phase 8: ETF membership",
        ]
        let durations: [Double] = [12.4, 384, 96, 1_820, 1_560, 940, 610, 4_210, 288]
        let building = variant == .building
        let stages: [[String: Any]] = labels.enumerated().map { i, label in
            var s: [String: Any] = ["key": "p\(i + 1)", "label": label, "stage_index": i]
            if building {
                if i < 4 {
                    s["status"] = "completed"
                    s["duration_sec"] = durations[i]
                } else if i == 4 {
                    s["status"] = "running"
                    s["message"] = "Resolving customer links for 214 of 503 companies"
                    s["substeps_completed"] = 2
                    s["total_substeps"] = 5
                } else {
                    s["status"] = "pending"
                }
            } else {
                s["status"] = i == 5 ? "skipped" : "completed"
                if i != 5 { s["duration_sec"] = durations[i] }
            }
            return s
        }
        return [
            "graph_built": !building,
            "control": [
                "running": building,
                "auto_update_enabled": true,
                "auto_update_interval_hours": 168,
                "auto_update_start_phase": 3,
                "auto_update_end_phase": 14,
                "next_auto_update_at": iso.string(from: now.addingTimeInterval(3 * 86_400)),
                "phase7_history_quarters": 4,
                "historical_mode_enabled": true,
                "historical_start_date": "2019-01-01",
            ],
            "graph_build": [
                "status": building ? "running" : "completed",
                "progress_pct": building ? 46.0 : 100.0,
                "eta_formatted": building ? "1h 12m" as Any : NSNull(),
                "current_phase_label": building ? "Phase 4: Customer links" as Any : NSNull(),
                "message": building ? "Building the supply-chain graph from 10-K filings" as Any : NSNull(),
                "last_updated": iso.string(from: now.addingTimeInterval(-26 * 3_600)),
                "stages": stages,
            ] as [String: Any],
            "graph_summary": [
                "node_counts": ["companies": 503, "edge_intervals": 48_210],
                "relationship_counts": [
                    ["key": "SUPPLIES", "label": "Supplier", "active_count": 6_412, "total_count": 7_980],
                    ["key": "CUSTOMER_OF", "label": "Customer", "active_count": 5_120, "total_count": 5_120],
                    ["key": "COMPETES_WITH", "label": "Competitor", "active_count": 2_940, "total_count": 3_310],
                    ["key": "HOLDS", "label": "13F holding", "active_count": 18_204, "total_count": 26_410],
                ],
            ],
            "scraper": ["status": building ? "running" : "completed", "index": building ? 214 : 503, "total_tickers": 503,
                        "edges_count": building ? 2_918 : 6_412, "progress_pct": building ? 42.5 : 100.0, "eta_formatted": building ? "38m" as Any : NSNull()],
            "bootstrap": [
                "enabled": true,
                "status": building ? "running" : "completed",
                "start_date": "2019-01-01",
                "coverage_end": "2026-11-11",
                "complete": !building,
                "completed_phases": building ? 1 : 3,
                "total_phases": 3,
                "duration_sec": 5_420,
                "completed_at": iso.string(from: now.addingTimeInterval(-26 * 3_600)),
                "started_at": iso.string(from: now.addingTimeInterval(-28 * 3_600)),
            ],
        ]
    }
}
