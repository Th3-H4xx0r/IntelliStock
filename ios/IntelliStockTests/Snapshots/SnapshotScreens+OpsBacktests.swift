import SwiftUI
import Testing
import UIKit
@testable import IntelliStock

/// Backtests and Strategies screens for the snapshot harness, as part of the
/// Ops suite (SnapshotScreens+Ops.swift). Names start `ops-`.
extension SnapshotScreensOps {
    // MARK: Backtests

    @Test func backtestsList() async throws {
        try await Snapshot.capture("ops-backtests", pages: 3, settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.backtests])
        }
    }

    /// The running run mid lookback (the web screenshot's #166220).
    @Test func backtestDetail() async throws {
        try await Snapshot.capture("ops-backtest-detail", pages: 9, settle: .seconds(5)) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.backtests, .backtest(SnapshotFixtures.opsRunningBacktestId)])
        }
    }

    /// A crypto run paused by an LLM failure: the pause card and the fees.
    @Test func backtestDetailPaused() async throws {
        try await Snapshot.capture("ops-backtest-detail-paused", pages: 4, settle: .seconds(5)) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.backtests, .backtest(SnapshotFixtures.opsFinishedBacktestId)])
        }
    }

    /// The strategy card and a per-stock accordion opened: the chart, the
    /// trade table and the decision trace. Hosted directly, since the
    /// harness cannot tap.
    @Test func backtestDetailExpanded() async throws {
        let env = Snapshot.makeServices()
        let repo = env.services.backtestRepository
        let id = SnapshotFixtures.opsRunningBacktestId
        let summary = try await repo.summary(id)
        let graph = try await repo.graphData(id)
        let schema = try #require(summary.strategySchema)
        try await Snapshot.capture("ops-backtest-detail-expanded", environment: env, pages: 4) { _ in
            NavigationStack {
                List {
                    BacktestStrategySection(schema: schema, strategyId: summary.strategyId, open: .constant(true))
                    DSSection("Stock charts and trades", subtitle: "\(summary.tickers.count) stocks") {
                        BacktestStockAccordion(
                            sym: "QQQ", summary: summary, graphData: graph, expanded: true,
                            decisionLimit: 2, onToggle: {}, onShowMoreDecisions: {}
                        )
                        BacktestStockAccordion(
                            sym: "GLD", summary: summary, graphData: graph, expanded: false,
                            decisionLimit: 2, onToggle: {}, onShowMoreDecisions: {}
                        )
                    }
                }
                .dsList()
                .navigationTitle("Backtest #\(id)")
                .navigationBarTitleDisplayMode(.inline)
            }
        }
    }

    @Test func backtestLogs() async throws {
        let env = Snapshot.makeServices()
        let model = BacktestDetailModel(id: SnapshotFixtures.opsRunningBacktestId, repository: { [services = env.services] in services.backtestRepository })
        try await Snapshot.capture("ops-backtest-logs", environment: env, settle: .seconds(3)) { _ in
            NavigationStack {
                BacktestLogsScreen(id: SnapshotFixtures.opsRunningBacktestId, model: model)
            }
        }
    }

    /// The replay, played a few days in at 5x (the model handed in, since
    /// the harness cannot tap Play).
    @Test func backtestPlayback() async throws {
        let env = Snapshot.makeServices()
        let id = SnapshotFixtures.opsRunningBacktestId
        let model = BacktestPlaybackModel(repository: { [services = env.services] in services.backtestRepository })
        await model.load(id)
        model.cycleSpeed() // 2x
        model.cycleSpeed() // 5x
        model.togglePlay()
        try await Snapshot.capture("ops-backtest-playback", environment: env, settle: .seconds(5)) { _ in
            NavigationStack { BacktestPlaybackView(id: id, model: model) }
        }
        model.stop()
    }

    /// Before play: the empty log and the transport.
    @Test func backtestPlaybackStart() async throws {
        try await Snapshot.capture("ops-backtest-playback-start", settle: .seconds(3)) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.backtests, .backtestPlayback(SnapshotFixtures.opsRunningBacktestId)])
        }
    }

    // MARK: Strategies

    @Test func strategiesList() async throws {
        try await Snapshot.capture("ops-strategies", pages: 3, settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .strategies)
        }
    }

    @Test func strategyDetail() async throws {
        try await Snapshot.capture("ops-strategy-detail", pages: 3, settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .strategies, stack: [.strategy(SnapshotFixtures.opsStrategyId)])
        }
    }

    @Test func strategyBacktestSheet() async throws {
        try await Snapshot.capture("ops-strategy-backtest-sheet", settle: .seconds(3)) { services in
            SnapshotHost.sheet {
                StrategyBacktestSheet(
                    strategyName: "F14 mirror",
                    linkedStrategyId: 207,
                    repository: { [services] in services.strategyRepository },
                    onQueued: { _ in }
                )
            }
        }
    }
}
