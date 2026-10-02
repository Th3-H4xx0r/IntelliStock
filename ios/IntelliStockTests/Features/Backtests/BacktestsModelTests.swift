import Foundation
import SwiftUI
import Testing
@testable import IntelliStock

/// Ported from test/features/backtests/backtest_test.dart (the status colour,
/// pagination and playback-speed groups), plus the list / detail / playback
/// controller logic.
struct BacktestStatusColorTests {
    @Test(arguments: [
        ("completed", DS.Palette.success), ("finished", DS.Palette.success), ("running", DS.Palette.success),
        ("queued", DS.Palette.warning), ("paused", DS.Palette.warning), ("paused_llm_critical", DS.Palette.warning),
        ("stopped", DS.Palette.danger), ("failed", DS.Palette.danger), ("error", DS.Palette.danger),
        ("cancelled", DS.Palette.danger), ("aborted_llm_failure", DS.Palette.danger),
        ("gibberish", Color.secondary), ("RUNNING", DS.Palette.success),
    ])
    func colorForStatus(_ status: String, _ color: Color) {
        #expect(StatusBadge.color(forStatus: status) == color)
    }

    @Test func nilIsSecondary() {
        #expect(StatusBadge.color(forStatus: nil) == Color.secondary)
    }
}

struct BacktestPaginationTests {
    @Test func upToSevenPagesIsAFlatList() {
        #expect(BacktestsListModel.buildPages(current: 1, total: 5) == [1, 2, 3, 4, 5])
        #expect(BacktestsListModel.buildPages(current: 3, total: 7) == [1, 2, 3, 4, 5, 6, 7])
    }

    @Test func tenPagesAtTheStartHasNoLeadingEllipsis() {
        let pages = BacktestsListModel.buildPages(current: 1, total: 10)
        #expect(pages == [1, 2, nil, 10])
    }

    @Test func tenPagesInTheMiddleHasBothEllipses() {
        let pages = BacktestsListModel.buildPages(current: 5, total: 10)
        #expect(pages == [1, nil, 4, 5, 6, nil, 10])
        #expect(pages.filter { $0 == nil }.count == 2)
    }

    @Test func tenPagesAtTheEndHasNoTrailingEllipsis() {
        #expect(BacktestsListModel.buildPages(current: 10, total: 10) == [1, nil, 9, 10])
    }
}

struct BacktestPlaybackSpeedTests {
    @Test func speedsAndDelays() {
        #expect(backtestPlaybackSpeeds == [0.5, 1, 2, 5, 10])
        #expect(BacktestPlaybackModel.frameDelayMs(2) == 500)
        #expect(BacktestPlaybackModel.frameDelayMs(0.5) == 2000)
        #expect(BacktestPlaybackModel.frameDelayMs(1) == 1000)
    }
}

@MainActor
struct BacktestsListModelTests {
    @Test func loadThenPollRefreshesActiveRowsAndReloadsOnTerminal() async {
        let stub = DataStub()
        stub.handler = { req in
            if req.url?.path == "/backtests" {
                return (200, #"{"backtests": [{"id": "1", "status": "running"}, {"id": "2", "status": "completed"}], "total": 2, "total_pages": 1, "page": 1}"#)
            }
            return (200, #"{"status": "completed", "progress": 100}"#)
        }
        let m = BacktestsListModel(repository: { BacktestRepository(client: stub.client) })
        await m.loadPage()
        #expect(m.rows.map(\.id) == ["1", "2"])
        #expect(m.hasActive)
        await m.pollRunning()
        #expect(m.statusMap["1"]?.status == "completed")
        let paths = stub.requests.map(\.path)
        #expect(paths.filter { $0 == "/backtests" }.count == 2)
        #expect(!paths.contains("/backtests/2/status"))
    }

    @Test func listQueryCarriesPagingAndSort() async {
        let stub = DataStub(json: #"{"backtests": [], "total": 0}"#)
        let m = BacktestsListModel(repository: { BacktestRepository(client: stub.client) })
        await m.setPerPage(25)
        #expect(stub.last?.queryItems == ["page": "1", "per_page": "25", "sort_by": "completed_at", "sort_order": "desc"])
        await m.toggleSort("completed_at")
        #expect(m.sortOrder == "asc")
        await m.toggleSort("pnl")
        #expect(m.sortBy == "pnl" && m.sortOrder == "desc")
    }

    @Test func failureKeepsRowsAndSetsError() async {
        let stub = DataStub(json: #"{"backtests": [{"id": "1"}], "total": 1}"#)
        let m = BacktestsListModel(repository: { BacktestRepository(client: stub.client) })
        await m.loadPage()
        stub.respond(status: 500, json: #"{"detail": "down"}"#)
        await m.refresh()
        #expect(m.rows.count == 1)
        #expect(m.error == "down")
        #expect(!m.loading)
    }

    @Test func performActionReturnsTheErrorText() async {
        let stub = DataStub(status: 400, json: #"{"detail": "cannot pause"}"#)
        let m = BacktestsListModel(repository: { BacktestRepository(client: stub.client) })
        #expect(await m.performAction("1", "pause") == "cannot pause")
        stub.respond(json: #"{"status": "paused"}"#)
        #expect(await m.performAction("1", "pause") == nil)
        #expect(m.statusMap["1"]?.status == "paused")
        #expect(stub.requests.contains { $0.method == "POST" && $0.path == "/backtests/1/pause" })
    }

    @Test func actionMetaCopy() {
        #expect(BacktestsListModel.actionMeta("pause").verb == "Pause")
        #expect(BacktestsListModel.actionMeta("resume").body == "The backtest will continue from where it was paused.")
        #expect(BacktestsListModel.actionMeta("other").verb == "Stop")
    }
}

@MainActor
struct BacktestDetailModelTests {
    @Test func activeSummaryFetchesStatusAndPolls() async {
        let stub = DataStub()
        stub.handler = { req in
            switch req.url?.path {
            case "/backtests/7/summary": return (200, #"{"id": "7", "status": "running", "tickers": ["AAPL"]}"#)
            case "/backtests/7/status": return (200, #"{"status": "running", "progress": 42}"#)
            case "/backtests/7/graph-data": return (500, #"{"detail": "x"}"#)
            default: return (200, #"{}"#)
            }
        }
        let m = BacktestDetailModel(id: "7", repository: { BacktestRepository(client: stub.client) })
        await m.load()
        #expect(!m.loading && m.error == nil)
        #expect(m.polling)
        #expect(m.progress == 42)
        #expect(m.graphData?.portfolioValueHistory.isEmpty == true)
        #expect(m.isActive)
    }

    @Test func terminalStatusStopsPollingAndRefetches() async {
        let stub = DataStub()
        stub.handler = { req in
            switch req.url?.path {
            case "/backtests/7/summary":
                return (200, #"{"status": "running"}"#)
            case "/backtests/7/status": return (200, #"{"status": "completed"}"#)
            default: return (200, #"{}"#)
            }
        }
        let m = BacktestDetailModel(id: "7", repository: { BacktestRepository(client: stub.client) })
        await m.load()
        #expect(!m.polling)
        #expect(m.currentStatus == "completed")
        #expect(m.isTerminal)
        #expect(stub.requests.map(\.path).filter { $0 == "/backtests/7/summary" }.count == 2)
    }

    @Test func rerunBodyMatchesTheDartMap() async throws {
        let stub = DataStub(json: #"{"tickers": ["AAPL", "MSFT"], "start_date": "2026-01-01", "end_date": "2026-02-01", "status": "completed", "instance": "inst-1"}"#)
        let m = BacktestDetailModel(id: "7", repository: { BacktestRepository(client: stub.client) })
        await m.load()
        let body = try #require(m.rerunBody())
        #expect(body.entries.map(\.key) == ["instance_id", "stocks", "start_date", "end_date", "granularity", "initial_cash", "emulate_fee_venue"])
        #expect(body["instance_id"] == "inst-1")
        #expect(body["granularity"] == "60")
        #expect(body["initial_cash"] == 100000)
        #expect(body["emulate_fee_venue"] == "default")
        stub.respond(json: #"{"backtest_id": 99}"#)
        #expect(try await m.rerun() == "99")
        #expect(stub.last?.method == "POST" && stub.last?.path == "/backtests")
    }

    @Test func deleteStopsPollingAndLogsLoad() async {
        let stub = DataStub(json: #"{"logs": ["a", "b"], "source": "file"}"#)
        let m = BacktestDetailModel(id: "7", repository: { BacktestRepository(client: stub.client) })
        await m.loadLogs()
        #expect(m.logLines == ["a", "b"] && m.logSource == "file")
        #expect(await m.performAction("delete") == nil)
        #expect(stub.last?.method == "DELETE" && stub.last?.path == "/backtests/7")
        #expect(!m.polling)
    }

    @Test func presentationHelpers() {
        #expect(BacktestDetailFormat.levelColor("ERROR boom") == DS.Palette.danger)
        #expect(BacktestDetailFormat.levelColor("retrying") == DS.Palette.warning)
        #expect(BacktestDetailFormat.levelColor("Trade profit") == DS.Palette.success)
        #expect(BacktestDetailFormat.levelColor("broker tick") == DS.Palette.info)
        #expect(BacktestDetailFormat.appliedLabel(venue: "kraken", rate: 0) == "Kraken")
        #expect(BacktestDetailFormat.appliedLabel(venue: nil, rate: 0.0025) == "Alpaca")
        #expect(BacktestDetailFormat.appliedLabel(venue: nil, rate: 0.5) == "—")
        #expect(BacktestDetailFormat.fmtBar("2026-06-10T14:00:00") == "2026-06-10")
        #expect(BacktestDetailFormat.fmtBar(nil) == "unknown")
        #expect(BacktestDetailFormat.truncateReason(String(repeating: "x", count: 361)).count == 360)
        #expect(BacktestDetailModel.actionMeta("delete").title == "Delete Backtest")
    }
}

@MainActor
struct BacktestPlaybackModelTests {
    private let payload = #"""
    {"events": [
      {"type": "date", "label": "Jan 2", "time": "09:30"},
      {"type": "portfolio", "value": 101000, "date": "2026-01-02T15:00:00Z", "holdings": [{"ticker": "AAPL", "qty": 3}]},
      {"type": "decision", "buys": [{"ticker": "AAPL", "qty": 3, "price": 190}]},
      {"type": "portfolio", "value": 102500.5, "date": "2026-01-03T15:00:00Z"}
    ], "metadata": {"initial_cash": 100000}}
    """#

    @Test func gettersFollowTheFrame() async {
        let stub = DataStub(json: payload)
        let m = BacktestPlaybackModel(repository: { BacktestRepository(client: stub.client) })
        await m.load("1")
        #expect(m.events.count == 4 && m.frameIndex == -1)
        #expect(m.currentDateLabel == "—")
        #expect(m.currentPortfolioValue == 100000)
        m.reset()
        #expect(m.frameIndex == 0)
        #expect(m.currentDateLabel == "Jan 2 — 09:30")
        #expect(m.chartPoints().count == 1)
        let range = m.xRange()
        #expect(range.min == DartDateTime.tryParse("2026-01-01T15:00:00Z"))
        #expect(range.max == DartDateTime.tryParse("2026-01-03T15:00:00Z"))
    }

    @Test func playAdvancesToTheEndThenStops() async {
        let stub = DataStub(json: payload)
        let m = BacktestPlaybackModel(repository: { BacktestRepository(client: stub.client) }, sleep: { _ in await Task.yield() })
        await m.load("1")
        m.togglePlay()
        #expect(await eventually { !m.isPlaying })
        #expect(m.frameIndex == 3)
        #expect(m.isFinished)
        #expect(m.currentPortfolioValue == 102500.5)
        #expect(m.currentHoldings.isEmpty)
        #expect(m.portfolioHistory.map(\.value) == [101000, 102500.5])
        // Play again from a finished state restarts at frame 0.
        m.togglePlay()
        #expect(m.frameIndex >= 0)
        m.togglePlay()
        #expect(!m.isPlaying)
    }

    @Test func speedCyclesAndLabels() {
        let m = BacktestPlaybackModel(repository: { BacktestRepository(client: DataStub().client) })
        #expect(m.speedLabel == "1x")
        m.cycleSpeed(); m.cycleSpeed(); m.cycleSpeed(); m.cycleSpeed()
        #expect(m.speedLabel == "0.5x")
        m.cycleSpeed()
        #expect(m.speedIndex == 1)
    }

    @Test func emptyAndError() async {
        let stub = DataStub(json: #"{"events": []}"#)
        let m = BacktestPlaybackModel(repository: { BacktestRepository(client: stub.client) })
        await m.load("1")
        #expect(m.isEmpty)
        stub.respond(status: 500, json: #"{"detail": "gone"}"#)
        let failing = BacktestPlaybackModel(repository: { BacktestRepository(client: stub.client) })
        await failing.load("1")
        #expect(failing.error == "gone")
    }
}
