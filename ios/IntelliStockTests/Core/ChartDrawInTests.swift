import Foundation
import Testing
@testable import IntelliStock

/// When a chart draws itself in (`chartDrawIn`): on first appearance and on a
/// new series, never on a poll, and at once under Reduce Motion.
struct ChartDrawInPolicyTests {
    private let key = AnyHashable(["acct-1", "1D"])

    @Test func firstAppearanceDrawsIn() {
        #expect(ChartDrawIn.step(drawn: nil, trigger: key, enabled: true, reduceMotion: false) == .animate)
    }

    @Test func theSameTriggerStaysStill() {
        // A poll tick, a list row scrolling back, a tab revisited.
        #expect(ChartDrawIn.step(drawn: key, trigger: key, enabled: true, reduceMotion: false) == .none)
        #expect(ChartDrawIn.step(drawn: key, trigger: key, enabled: false, reduceMotion: true) == .none)
    }

    @Test func aNewSeriesDrawsInAgain() {
        let week = AnyHashable(["acct-1", "1W"])
        let otherAccount = AnyHashable(["acct-2", "1D"])
        #expect(ChartDrawIn.step(drawn: key, trigger: week, enabled: true, reduceMotion: false) == .animate)
        #expect(ChartDrawIn.step(drawn: key, trigger: otherAccount, enabled: true, reduceMotion: false) == .animate)
    }

    @Test func reduceMotionOrDisabledShowsAtOnce() {
        #expect(ChartDrawIn.step(drawn: nil, trigger: key, enabled: true, reduceMotion: true) == .show)
        #expect(ChartDrawIn.step(drawn: nil, trigger: key, enabled: false, reduceMotion: false) == .show)
        #expect(ChartDrawIn.step(drawn: AnyHashable(["acct-1", "1W"]), trigger: key, enabled: true, reduceMotion: true) == .show)
    }

    @Test func theDefaultTriggerDrawsInOnAppearanceOnly() {
        let appearOnly = AnyHashable(0)
        #expect(ChartDrawIn.step(drawn: nil, trigger: appearOnly, enabled: true, reduceMotion: false) == .animate)
        #expect(ChartDrawIn.step(drawn: appearOnly, trigger: appearOnly, enabled: true, reduceMotion: false) == .none)
    }

    @Test func dashboardKeyFollowsAccountAndRangeOnly() {
        let a = DashboardChartGeometry.drawInKey(accountId: "acct-1", range: "1D")
        #expect(a == DashboardChartGeometry.drawInKey(accountId: "acct-1", range: "1D"))
        #expect(a != DashboardChartGeometry.drawInKey(accountId: "acct-1", range: "1W"))
        #expect(a != DashboardChartGeometry.drawInKey(accountId: "acct-2", range: "1D"))
    }
}

/// How much of the width the mask uncovers.
struct ChartDrawInRevealTests {
    @Test func restIsFullyUncovered() {
        #expect(ChartDrawIn.revealed(phase: 3, target: 3, pending: false, complete: false) == 1)
    }

    @Test func aReplayStartsCoveredAndRunsToFull() {
        #expect(ChartDrawIn.revealed(phase: 2, target: 3, pending: false, complete: false) == 0)
        #expect(abs(ChartDrawIn.revealed(phase: 2.4, target: 3, pending: false, complete: false) - 0.4) < 1e-9)
        #expect(ChartDrawIn.revealed(phase: 3, target: 3, pending: false, complete: false) == 1)
    }

    @Test func anInterruptedReplayClampsAtZero() {
        // A second switch mid-draw: the edge waits at 0 until it catches up.
        #expect(ChartDrawIn.revealed(phase: 1.3, target: 3, pending: false, complete: false) == 0)
    }

    @Test func pendingHidesUntilTheDrawStarts() {
        // The new series never flashes in whole for a frame first.
        #expect(ChartDrawIn.revealed(phase: 3, target: 3, pending: true, complete: false) == 0)
    }

    @Test func aScrubUncoversEverything() {
        #expect(ChartDrawIn.revealed(phase: 2.1, target: 3, pending: false, complete: true) == 1)
        #expect(ChartDrawIn.revealed(phase: 0, target: 0, pending: true, complete: true) == 1)
    }
}

/// A gate a stubbed request waits on, so a test can look at a model while a
/// range switch is still in flight.
nonisolated private final class ChartDrawInGate: @unchecked Sendable {
    private let lock = NSLock()
    private var open = false

    func release() { lock.withLock { open = true } }

    func wait() {
        let deadline = Date().addingTimeInterval(5)
        while !lock.withLock({ open }), Date() < deadline {
            usleep(5_000)
        }
    }
}

/// The models record which range their data belongs to, so the charts draw
/// the new range in when its data lands, not on the tap.
@Suite(.serialized)
struct ChartDrawInLoadedRangeTests {
    @Test func tokenUsageLoadedRangeLagsASwitchUntilTheDataLands() async {
        let gate = ChartDrawInGate()
        let stub = DataStub()
        stub.handler = { req in
            if req.url?.query?.contains("range=7d") == true { gate.wait() }
            return (200, "{}")
        }
        let client = stub.client
        let model = TokenUsageModel(repository: { TokenUsageRepository(client: client) })
        #expect(model.loadedRange == nil)
        await model.refreshNow()
        #expect(model.loadedRange == "24h")

        let task = Task { await model.setRange("7d") }
        #expect(await eventually { stub.requests.contains { $0.url?.query?.contains("range=7d") == true } })
        #expect(model.range == "7d")
        #expect(model.loadedRange == "24h")
        gate.release()
        await task.value
        #expect(model.loadedRange == "7d")
    }

    @Test func liveHistoryRangesLagASwitchUntilTheDataLands() async {
        let gate = ChartDrawInGate()
        let stub = DataStub()
        stub.handler = { req in
            if req.url?.query?.contains("range=1M") == true { gate.wait() }
            switch req.path {
            case "/instances/i1/live-state":
                return (200, #"{"status": "active", "equity": 1000, "positions": [{"symbol": "AAPL", "qty": 1}]}"#)
            case "/instances/i1/portfolio-history":
                return (200, #"{"timestamps": [1, 2], "values": [1, 2]}"#)
            case "/symbol-historicals":
                return (200, #"{"results": {"AAPL": [{"ts": 1, "value": 3}]}}"#)
            default:
                return (404, #"{"detail": "Not Found"}"#)
            }
        }
        let model = LiveTradingModel(instanceId: "i1", repository: { LiveRepository(client: stub.client) })
        await model.load()
        await model.pollCycle()
        #expect(model.value?.equityHistoryRange == "1D")
        #expect(model.value?.positionHistoricalsRange == "1D")

        let task = Task { await model.setRange("1M") }
        #expect(await eventually { stub.requests.contains { $0.url?.query?.contains("range=1M") == true } })
        #expect(model.value?.currentRange == "1M")
        #expect(model.value?.equityHistoryRange == "1D")
        gate.release()
        await task.value
        #expect(model.value?.equityHistoryRange == "1M")
        #expect(model.value?.positionHistoricalsRange == "1M")
    }
}
