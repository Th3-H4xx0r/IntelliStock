import Foundation
import Testing
@testable import IntelliStock

/// The rank-medal group of strategy_config_test.dart, plus the strategies
/// controller logic and the backtest sheet.
struct StrategyRankTests {
    @Test func medalForRanksOneToThreeThenHash() {
        #expect(StrategyRank.medal(1) == "🥇")
        #expect(StrategyRank.medal(2) == "🥈")
        #expect(StrategyRank.medal(3) == "🥉")
        #expect(StrategyRank.medal(4) == "#4")
        #expect(StrategyRank.medal(5) == "#5")
    }

    @Test func pageWindowFollowsTheDartRule() {
        #expect(StrategiesModel.pageWindow(page: 1, totalPages: 3) == [1, 2, 3])
        #expect(StrategiesModel.pageWindow(page: 2, totalPages: 9) == [1, 2, 3, 4, 5])
        #expect(StrategiesModel.pageWindow(page: 8, totalPages: 9) == [5, 6, 7, 8, 9])
        #expect(StrategiesModel.pageWindow(page: 5, totalPages: 9) == [3, 4, 5, 6, 7])
    }
}

@MainActor
struct StrategiesModelTests {
    private func stub() -> DataStub {
        let stub = DataStub()
        stub.handler = { req in
            switch req.url?.path {
            case "/strategies": return (200, #"{"strategies": [{"id": 1, "name": "beta", "strategies": [{"strategy": "rsi"}]}, {"id": 2, "name": "Alpha", "strategies": []}, {"id": 3, "name": "gamma"}]}"#)
            case "/agent/results": return (200, #"{"results": [{"backtest_id": "b1", "strategy_id": 1, "overall_profit": 50, "pnl_percent": 5}, {"backtest_id": "b2", "strategy_id": 1, "overall_profit": 80, "pnl_percent": 2}, {"backtest_id": "b3", "strategy_id": 2, "overall_profit": -10, "pnl_percent": -1}]}"#)
            case "/agent/top5": return (200, #"{"top5": [{"rank": 2, "strategy_id": 2, "backtest_id": "t2"}, {"rank": 1, "strategy_id": 1, "backtest_id": "t1", "strategy_snapshot": {"name": "Snap", "strategies": [{"type": "momentum"}, 3]}}, {"strategy_id": 9}]}"#)
            default: return (500, #"{"detail": "no"}"#)
            }
        }
        return stub
    }

    @Test func fetchAllMergesSortsAndRanks() async {
        let s = stub()
        let m = StrategiesModel(repository: { StrategyRepository(client: s.client) })
        await m.fetchAll()
        #expect(!m.loading && m.error == nil)
        #expect(m.top5.map { $0["strategy_id"] } == [1, 2, 9])
        // Best P&L descending, nil last.
        #expect(m.rows.map(\.id) == [1, 2, 3])
        #expect(m.rows[0].bestPnl == 80 && m.rows[0].bestPnlBid == "b2" && m.rows[0].bestPct == 5)
        #expect(m.rows[0].rank == 1)
        m.setSort(.name)
        #expect(m.rows.map(\.name) == ["gamma", "beta", "Alpha"])
        m.setSort(.name)
        #expect(m.rows.map(\.name) == ["Alpha", "beta", "gamma"])
        m.setSort(.backtests)
        #expect(m.rows.first?.id == 1)
    }

    @Test func top5EnrichedNamesAndSubs() async {
        let s = stub()
        let m = StrategiesModel(repository: { StrategyRepository(client: s.client) })
        await m.fetchAll()
        let e = m.top5Enriched
        #expect(e.map(\.name) == ["Snap", "Strategy #2", "Strategy #9"])
        #expect(e[0].subs == ["momentum", "?"])
        #expect(e[2].rank == 5)
    }

    @Test func pagingClampsAndResets() async {
        let s = stub()
        let m = StrategiesModel(repository: { StrategyRepository(client: s.client) })
        await m.fetchAll()
        m.setPerPage(10)
        #expect(m.totalPages == 1 && m.pagedRows.count == 3)
        m.setPage(2)
        #expect(m.pagedRows.isEmpty)
        m.setSort(.bestPct)
        #expect(m.page == 1)
    }
}

@MainActor
struct StrategyDetailModelTests {
    @Test func loadSortsBacktestsAndFlagsAgentBest() async {
        let stub = DataStub()
        stub.handler = { req in
            switch req.url?.path {
            case "/strategies/7": return (200, #"{"strategy": {"id": 7, "name": "S", "strategies": [{"strategy": "rsi", "decision_phase": "entry", "weight": 1}]}}"#)
            case "/agent/results": return (200, #"{"results": [{"backtest_id": "a", "strategy_id": 7, "overall_profit": 5, "created_at": "2026-01-02"}, {"backtest_id": "b", "strategy_id": 7, "overall_profit": 9, "created_at": "2026-01-01"}, {"backtest_id": "c", "strategy_id": 8}]}"#)
            default: return (200, #"{"backtest_id": 7}"#)
            }
        }
        let m = StrategyDetailModel(strategyId: "7", repository: { StrategyRepository(client: stub.client) })
        await m.load()
        #expect(m.strategy?.name == "S")
        #expect(m.isAgentBest)
        #expect(m.strategyBacktests.map(\.backtestId) == ["a", "b"])
        #expect(m.sortedBacktests.map(\.backtestId) == ["a", "b"])
        m.setBtSort("pnl")
        #expect(m.sortedBacktests.map(\.backtestId) == ["b", "a"])
        m.setBtSort("pnl")
        #expect(m.sortedBacktests.map(\.backtestId) == ["a", "b"])
        #expect(m.bestPnlBacktest?.backtestId == "b")
        #expect(StrategyDetailModel.phaseColor("ENTRY") == DS.Palette.success)
    }

    @Test func missingStrategyIsAnError() async {
        let stub = DataStub(status: 404, json: #"{"detail": "Not found"}"#)
        let m = StrategyDetailModel(strategyId: "7", repository: { StrategyRepository(client: stub.client) })
        await m.load()
        #expect(m.strategy == nil)
        #expect(m.error == "Not found")
    }
}

@MainActor
struct StrategyBacktestFormModelTests {
    private func model(_ stub: DataStub, linked: Int? = 7) -> StrategyBacktestFormModel {
        StrategyBacktestFormModel(
            strategyName: "S",
            linkedStrategyId: linked,
            now: { Date(timeIntervalSince1970: 1_000.123) },
            pause: { _ in },
            repository: { StrategyRepository(client: stub.client) }
        )
    }

    @Test func defaultSelectionPrefersLinkedThenFree() async {
        let stub = DataStub(json: #"{"instances": [{"id": "free1", "strategy_id": null}, {"id": "lnk", "strategy_id": 7}, {"id": "other", "strategy_id": 3}]}"#)
        let m = model(stub)
        await m.loadInstances()
        #expect(m.linkedInstances.map(StrategyBacktestFormModel.instanceId) == ["lnk"])
        #expect(m.freeInstances.map(StrategyBacktestFormModel.instanceId) == ["free1"])
        #expect(m.selectedInstId == "lnk")
    }

    @Test func validationCopy() async {
        let m = model(DataStub())
        #expect(await m.submit() == nil)
        #expect(m.msg == "At least one stock is required")
        m.stocks = " aapl, ,msft "
        #expect(await m.submit() == nil)
        #expect(m.msg == "Start date is required")
        m.start = "2026-02-01"
        #expect(await m.submit() == nil)
        #expect(m.msg == "End date is required")
        m.end = "2026-02-01"
        #expect(await m.submit() == nil)
        #expect(m.msg == "End date must be after start date")
        m.end = "2026-03-01"
        #expect(await m.submit() == nil)
        #expect(m.msg == "Instance name is required when creating a new one")
    }

    @Test func newInstanceIsCreatedLinkedAndBacktested() async throws {
        let stub = DataStub()
        stub.handler = { req in
            switch req.url?.path {
            case "/instances": return (200, #"{"id": "101000"}"#)
            case "/backtests": return (200, #"{"backtest_id": 44}"#)
            default: return (200, "{}")
            }
        }
        let m = model(stub)
        m.stocks = "aapl, msft"
        m.start = "2026-01-01"
        m.end = "2026-02-01"
        m.newInstName = " Test "
        m.cash = "2500"
        #expect(await m.submit() == "44")
        #expect(m.msg == "Backtest #44 queued!" && m.msgOk)
        let reqs = stub.requests
        #expect(reqs.map(\.path) == ["/instances", "/instances/101000/link-strategy", "/backtests"])
        #expect(reqs[0].jsonBody == ["id": "200123", "name": "Test", "run_command": false])
        #expect(reqs[1].jsonBody == ["strategy_id": 7])
        #expect(reqs[2].jsonBody == [
            "instance_id": "101000", "stocks": ["AAPL", "MSFT"], "start_date": "2026-01-01",
            "end_date": "2026-02-01", "granularity": "86400", "initial_cash": 2500.0,
        ])
    }

    @Test func alreadyLinkedInstanceSkipsTheLink() async {
        let stub = DataStub()
        stub.handler = { req in
            switch req.url?.path {
            case "/instances": return (200, #"{"instances": [{"id": "lnk", "strategy_id": 7}]}"#)
            default: return (200, #"{"id": "b9"}"#)
            }
        }
        let m = model(stub)
        await m.loadInstances()
        m.stocks = "AAPL"
        m.start = "2026-01-01"
        m.end = "2026-02-01"
        #expect(await m.submit() == "b9")
        #expect(!stub.requests.contains { $0.path.hasSuffix("/link-strategy") })
    }
}
