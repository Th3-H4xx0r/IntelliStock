import Foundation
import Testing
@testable import IntelliStock

/// Ported from test/polling_test.dart (`fakeAsync` → `ManualClock`), plus the
/// lifecycle pause behaviour `PollingNotifier` relied on (Review Focus 4).
@MainActor
struct PollingLoopTests {
    @Test func firesOnEachInterval() async {
        let clock = ManualClock()
        var count = 0
        let loop = PollingLoop(interval: { .seconds(5) }, sleep: clock.sleep) { count += 1 }
        loop.start()
        await clock.advance(by: .seconds(16))
        loop.dispose()
        #expect(count == 3) // at 5 s, 10 s, 15 s
    }

    @Test func pauseStopsFiringAndResumeRestarts() async {
        let clock = ManualClock()
        var count = 0
        let loop = PollingLoop(interval: { .seconds(5) }, sleep: clock.sleep) { count += 1 }
        loop.start()
        await clock.advance(by: .seconds(6)) // 1 fire
        loop.pause()
        await clock.advance(by: .seconds(20)) // no fires
        #expect(count == 1)
        loop.resume()
        await clock.advance(by: .seconds(6)) // 1 more, a full interval after resume
        loop.dispose()
        #expect(count == 2)
    }

    @Test func disposeStopsFiring() async {
        let clock = ManualClock()
        var count = 0
        let loop = PollingLoop(interval: { .seconds(5) }, sleep: clock.sleep) { count += 1 }
        loop.start()
        loop.dispose()
        await clock.advance(by: .seconds(30))
        #expect(count == 0)
    }

    @Test func intervalIsReReadEveryCycle() async {
        let clock = ManualClock()
        var interval: Duration = .seconds(3)
        var count = 0
        let loop = PollingLoop(interval: { interval }, sleep: clock.sleep) {
            count += 1
            interval = .seconds(10)
        }
        loop.start()
        await clock.advance(by: .seconds(14))
        loop.dispose()
        #expect(count == 2) // 3 s, then 13 s
        #expect(Array(clock.requested.prefix(3)) == [.seconds(3), .seconds(10), .seconds(10)])
    }

    @Test func aFailingFetchNeverStopsTheLoop() async {
        struct Boom: Error {}
        let clock = ManualClock()
        var count = 0
        let loop = PollingLoop(interval: { .seconds(5) }, sleep: clock.sleep) {
            count += 1
            throw Boom()
        }
        loop.start()
        await clock.advance(by: .seconds(16))
        loop.dispose()
        #expect(count == 3)
    }

    @Test func runPausesInTheBackgroundAndResumesInTheForeground() async {
        let clock = ManualClock()
        let lifecycle = AppLifecycle()
        var count = 0
        let loop = PollingLoop(interval: { .seconds(5) }, sleep: clock.sleep) { count += 1 }
        let task = Task { await loop.run(lifecycle: lifecycle) }
        await clock.advance(by: .seconds(6))
        #expect(count == 1)

        lifecycle.handle(.background)
        await clock.settle()
        #expect(loop.isPaused)
        await clock.advance(by: .seconds(60))
        #expect(count == 1)

        lifecycle.handle(.active)
        await clock.advance(by: .seconds(6))
        #expect(count == 2)

        task.cancel()
        await clock.settle()
        await clock.advance(by: .seconds(30))
        #expect(count == 2)
    }

    @Test func runStartsPausedWhenLaunchedInTheBackground() async {
        let clock = ManualClock()
        let lifecycle = AppLifecycle(isForeground: false)
        var count = 0
        let loop = PollingLoop(interval: { .seconds(5) }, sleep: clock.sleep) { count += 1 }
        let task = Task { await loop.run(lifecycle: lifecycle) }
        await clock.advance(by: .seconds(20))
        #expect(count == 0)
        lifecycle.handle(.active)
        await clock.advance(by: .seconds(5))
        #expect(count == 1)
        task.cancel()
    }

    @Test func lifecycleRecordsLastPausedAt() {
        let moment = Date(timeIntervalSince1970: 42)
        let lifecycle = AppLifecycle(now: { moment })
        #expect(lifecycle.isForeground)
        lifecycle.handle(.inactive)
        #expect(!lifecycle.isForeground)
        #expect(lifecycle.lastPausedAt == moment)
        lifecycle.handle(.active)
        #expect(lifecycle.isForeground)
    }
}

struct ParseLogLineTests {
    @Test func extractsTimestampAndMessage() throws {
        let l = parseLogLine("[2026-01-01 12:00:00] hello world")
        #expect(l.message == "hello world")
        let ts = try #require(l.ts)
        #expect(DartDateTime.localCalendar.component(.hour, from: ts) == 12)
    }

    @Test func unparseableTimestampKeepsTheMessage() {
        let l = parseLogLine("[worker-3] started")
        #expect(l.ts == nil)
        #expect(l.message == "started")
    }

    @Test func noBracketMeansTheWholeLineIsTheMessage() {
        let l = parseLogLine("plain line")
        #expect(l.ts == nil)
        #expect(l.message == "plain line")
        #expect(l.raw == "plain line")
    }

    @Test func classifiesError() { #expect(parseLogLine("Traceback: boom").level == .error) }
    @Test func classifiesWarn() { #expect(parseLogLine("retrying connection").level == .warn) }
    @Test func classifiesSuccess() { #expect(parseLogLine("build completed").level == .success) }
    @Test func classifiesBrokerAsInfo() { #expect(parseLogLine("Broker connected").level == .info) }
    @Test func normalOtherwise() { #expect(parseLogLine("just a line").level == .normal) }

    @Test func keywordOrderMatchesDart() {
        // "failed" beats "retry"; "skip" beats "success".
        #expect(parseLogLine("retry failed").level == .error)
        #expect(parseLogLine("skip success").level == .warn)
        #expect(parseLogLine("all ok").level == .success)
    }

    @Test func colorsFollowTheLevel() {
        #expect(parseLogLine("error").color == DS.Palette.danger)
        #expect(parseLogLine("warn").color == DS.Palette.warning)
        #expect(parseLogLine("passed").color == DS.Palette.success)
        #expect(parseLogLine("broker").color == DS.Palette.info)
    }
}

/// `LogTailer` against a stubbed `live-logs` endpoint.
@Suite(.serialized)
@MainActor
struct LogTailerTests {
    init() { StubURLProtocol.reset() }

    private func tailer(_ clock: ManualClock) -> LogTailer {
        let client = ApiClient(baseURL: "https://api.example.test", tokens: nil, session: StubURLProtocol.session)
        return LogTailer(
            client: client,
            pathBuilder: { "/instances/i1/live-logs?since_line=\($0)" },
            sleep: clock.sleep
        )
    }

    private nonisolated static func body(_ json: JSON) -> Data { (try? json.data()) ?? Data() }

    /// Answers by cursor: `pages[since_line]`, else an empty running page.
    private func serve(_ pages: [Int: JSON], status: Int = 200) {
        StubURLProtocol.handler = { request in
            let since = Int(request.queryItems["since_line"] ?? "") ?? -1
            let page = pages[since] ?? ["logs": [], "next_line": .int(since), "final_status": "running"]
            return (status, ["Content-Type": "application/json"], Self.body(page))
        }
    }

    @Test func tailsFromTheCursorAtTheRunningCadence() async {
        serve([
            0: ["logs": ["[2026-01-01 12:00:00] hello", "error boom"], "next_line": 2, "total_lines": 2,
                "final_status": "running", "id": "b1"],
            2: ["logs": ["line3"], "next_line": 3, "total_lines": 3, "final_status": "running", "id": "b1"],
        ])
        let clock = ManualClock()
        let t = tailer(clock)
        t.start()
        #expect(await eventually { t.state.lines.count == 2 })
        #expect(t.state.loading == false)
        #expect(t.state.nextLine == 2)
        #expect(t.state.lines.map(\.level) == [.normal, .error])
        #expect(t.state.buildId == "b1")
        #expect(clock.requested.last == .seconds(5))

        await clock.advance(by: .seconds(5))
        #expect(await eventually { t.state.lines.count == 3 })
        #expect(t.state.nextLine == 3)
        #expect(StubURLProtocol.requests.map { $0.url?.path } == ["/instances/i1/live-logs", "/instances/i1/live-logs"])
        #expect(StubURLProtocol.requests.last?.queryItems["since_line"] == "2")
        t.dispose()
    }

    @Test func idleStatusUsesTheIdleInterval() async {
        serve([0: ["logs": ["done"], "next_line": 1, "final_status": "completed"]])
        let clock = ManualClock()
        let t = tailer(clock)
        t.start()
        #expect(await eventually { t.state.finalStatus == "completed" })
        await clock.settle()
        #expect(clock.requested.last == .seconds(15))
        t.dispose()
    }

    @Test func truncatedRepollsImmediately() async {
        serve([
            0: ["logs": ["a"], "next_line": 1, "truncated": true, "final_status": "running"],
            1: ["logs": ["b"], "next_line": 2, "final_status": "running"],
        ])
        let clock = ManualClock()
        let t = tailer(clock)
        t.start()
        #expect(await eventually { t.state.lines.count == 2 })
        #expect(clock.requested.first == .zero)
        t.dispose()
    }

    @Test func errorsBackOffTwoFiveTenThirty() async {
        StubURLProtocol.respond(status: 500, json: #"{"detail": "nope"}"#)
        let clock = ManualClock()
        let t = tailer(clock)
        t.start()
        #expect(await eventually { t.state.error != nil })
        #expect(t.state.loading == false)
        #expect(clock.requested == [.seconds(2)])

        for (count, expected) in [(2, Duration.seconds(5)), (3, .seconds(10)), (4, .seconds(30)), (5, .seconds(30))] {
            await clock.advance(by: clock.requested.last ?? .zero)
            #expect(await eventually { clock.requested.count == count })
            #expect(clock.requested.last == expected)
        }
        t.dispose()
    }

    @Test func successClearsTheErrorAndResetsTheBackoff() async {
        StubURLProtocol.respond(status: 503)
        let clock = ManualClock()
        let t = tailer(clock)
        t.start()
        #expect(await eventually { t.state.error != nil })
        serve([0: ["logs": ["ok"], "next_line": 1, "final_status": "running"]])
        await clock.advance(by: .seconds(2))
        #expect(await eventually { t.state.lines.count == 1 })
        #expect(t.state.error == nil)
        #expect(clock.requested.last == .seconds(5))
        t.dispose()
    }

    @Test func aNewBuildIdReseedsTheLines() async {
        serve([
            0: ["logs": ["old1", "old2"], "next_line": 2, "final_status": "running", "id": "b1"],
            2: ["logs": ["new1"], "next_line": 1, "final_status": "running", "id": "b2"],
        ])
        let clock = ManualClock()
        let t = tailer(clock)
        t.start()
        #expect(await eventually { t.state.lines.count == 2 })
        await clock.advance(by: .seconds(5))
        #expect(await eventually { t.state.buildId == "b2" })
        #expect(t.state.lines.map(\.raw) == ["new1"])
        #expect(t.state.nextLine == 1)
        t.dispose()
    }

    @Test func capsAtTenThousandLines() async {
        let logs = (0...10_000).map { JSON.string(String($0)) }
        serve([0: ["logs": .array(logs), "next_line": 10_001, "final_status": "running"]])
        let clock = ManualClock()
        let t = tailer(clock)
        t.start()
        #expect(await eventually { !t.state.lines.isEmpty })
        #expect(t.state.lines.count == 10_000)
        #expect(t.state.lines.first?.raw == "1")
        #expect(t.state.lines.last?.raw == "10000")
        #expect(t.state.totalLines == 10_000)
        t.dispose()
    }

    @Test func pauseStopsAndResumePollsAtOnce() async {
        serve([:])
        let clock = ManualClock()
        let t = tailer(clock)
        t.start()
        #expect(await eventually { StubURLProtocol.requests.count == 1 })
        t.pause()
        await clock.advance(by: .seconds(60))
        #expect(StubURLProtocol.requests.count == 1)
        t.resume()
        #expect(await eventually { StubURLProtocol.requests.count == 2 })
        t.dispose()
    }

    @Test func aNonStringLogEntryIsAnError() async {
        serve([0: ["logs": ["fine", 7], "next_line": 2]])
        let clock = ManualClock()
        let t = tailer(clock)
        t.start()
        #expect(await eventually { t.state.error != nil })
        #expect(t.state.lines.isEmpty)
        t.dispose()
    }
}
