import Foundation
import Testing
@testable import IntelliStock

// The wheel regrade (backend swing_trader/wheel_manual.py): the signal row's
// `regrade` block and `source`, the card's pure helpers, and the model's
// request and 4 s poll.

private let askedAt = "2026-11-20T15:00:00+00:00"
private let asked = swingUTC(2026, 11, 20, 15, 0)

private let queued: JSON = ["status": "queued", "requested_at": .string(askedAt), "requested_by": "pranav", "count": 0, "error": nil]

private func done(_ fields: JSONObject = [:]) -> JSON {
    var block: JSONObject = [
        "status": "done", "requested_at": .string(askedAt), "requested_by": "pranav",
        "finished_at": "2026-11-20T15:01:00+00:00", "count": 1, "error": nil,
        "qualifies": true, "rescored": true, "reason": nil,
        "previous": [
            "score": 62, "recommendation": "REVIEW", "stock_price": 140, "strike": 130,
            "premium_est": 1.3, "expiry": "2026-11-20", "at": "2026-11-16T15:42:00.123456+00:00",
        ],
    ]
    for (key, value) in fields { block[key] = value }
    return .object(block)
}

// MARK: - Decoding

struct SwingRegradeDecodingTests {
    @Test func aFinishedRegradeDecodesEveryField() throws {
        let s = regradeTestSignal(regrade: done([
            "count": 2, "qualifies": false, "reason": "score 40 is below the review threshold",
            "finished_at": "2026-11-20T15:01:10.5+00:00", "scored_at": "2026-11-20T15:01:10.5+00:00",
        ]))
        let r = try #require(s.regrade)
        #expect(r.status == "done")
        #expect(r.requestedAt == asked)
        #expect(r.requestedBy == "pranav")
        #expect(r.finishedAt == swingUTC(2026, 11, 20, 15, 1, 10).addingTimeInterval(0.5))
        #expect(r.scoredAt == swingUTC(2026, 11, 20, 15, 1, 10).addingTimeInterval(0.5))
        #expect(r.count == 2)
        #expect(r.error == "")
        #expect(r.qualifies == false)
        #expect(r.rescored == true)
        #expect(r.reason == "score 40 is below the review threshold")
        let p = try #require(r.previous)
        #expect(p.score == 62)
        #expect(p.recommendation == "REVIEW")
        #expect(p.stockPrice == 140)
        #expect(p.strike == 130)
        #expect(p.premiumEst == 1.3)
        #expect(p.expiry == "2026-11-20")
        #expect(p.at == swingUTC(2026, 11, 16, 15, 42).addingTimeInterval(0.123456))
    }

    @Test func aMissingOrMalformedBlockNeverFailsTheRow() throws {
        #expect(regradeTestSignal().regrade == nil)
        #expect(regradeTestSignal(regrade: "queued").regrade == nil)
        #expect(regradeTestSignal(regrade: nil).regrade == nil)
        #expect(regradeTestSignal(regrade: "queued").id == "w1")

        let empty = try #require(regradeTestSignal(regrade: [:]).regrade)
        #expect(empty.status == "")
        #expect(empty.requestedAt == nil)
        #expect(empty.finishedAt == nil)
        #expect(empty.scoredAt == nil)
        #expect(empty.count == 0)
        #expect(empty.error == "")
        #expect(empty.qualifies == nil)
        #expect(empty.rescored == nil)
        #expect(empty.reason == "")
        #expect(empty.previous == nil)

        let odd = try #require(regradeTestSignal(regrade: [
            "status": 3, "count": "2", "qualifies": "false", "rescored": 1,
            "requested_at": "yesterday", "error": "  boom  ", "previous": "x", "scored_at": 7,
        ]).regrade)
        #expect(odd.count == 2)
        #expect(odd.qualifies == nil)
        #expect(odd.rescored == nil)
        #expect(odd.requestedAt == nil)
        #expect(odd.scoredAt == nil)
        #expect(odd.error == "boom")
        #expect(odd.previous == nil)

        let partial = try #require(regradeTestSignal(regrade: ["status": "done", "previous": ["score": "71"]]).regrade)
        #expect(partial.previous?.score == 71)
        #expect(partial.previous?.stockPrice == nil)
        #expect(partial.previous?.at == nil)
    }

    @Test func sourceMarksAManualRunCard() {
        #expect(regradeTestSignal(source: "manual_scan").isManualRun)
        #expect(regradeTestSignal(source: "manual_scan").source == "manual_scan")
        #expect(!regradeTestSignal().isManualRun)
        #expect(regradeTestSignal().source == "")
        #expect(!regradeTestSignal(source: "weekly").isManualRun)
    }

    @Test func onlyAPendingWheelCardCanBeRegraded() {
        #expect(regradeTestSignal().canRegrade)
        #expect(!withTestStatus(regradeTestSignal(), "approved").canRegrade)
        #expect(!swingTestSignal("s1").canRegrade)
    }
}

// MARK: - Presentation

struct SwingRegradePresentationTests {
    @Test func aQueuedOrRunningRegradeIsLiveForUnderFiveMinutes() {
        for status in ["queued", "running"] {
            let s = regradeTestSignal(regrade: ["status": .string(status), "requested_at": .string(askedAt)])
            #expect(swingRegradePhase(s, now: asked.addingTimeInterval(299)) == .live)
            #expect(swingRegradePhase(s, now: asked.addingTimeInterval(300)) == .stale)
            #expect(swingRegradePhase(s, now: asked.addingTimeInterval(301)) == .stale)
            // A device clock behind the server's still reads it live.
            #expect(swingRegradePhase(s, now: asked.addingTimeInterval(-30)) == .live)
        }
        // Undated: nothing says it is still alive.
        #expect(swingRegradePhase(regradeTestSignal(regrade: ["status": "queued"]), now: asked) == .stale)
    }

    @Test func doneFailedAndNone() {
        #expect(swingRegradePhase(regradeTestSignal(regrade: done()), now: asked) == .done)
        #expect(swingRegradePhase(regradeTestSignal(regrade: ["status": "failed", "error": "x"]), now: asked) == .failed)
        #expect(swingRegradePhase(regradeTestSignal(), now: asked) == .none)
        #expect(swingRegradePhase(regradeTestSignal(regrade: [:]), now: asked) == .none)
        #expect(swingRegradePhase(regradeTestSignal(regrade: ["status": "weird"]), now: asked) == .none)
    }

    /// `regrade.scored_at` is when the card's current score was made (a
    /// rescored regrade sets it, every later block carries it); without it,
    /// `created_at`.
    @Test func aCardWasScoredAtScoredAtElseWhenItWasCreated() {
        #expect(swingScoredAtText(regradeTestSignal()) == "Scored Mon 16 Nov, 10:42 ET · stock at $140.00")
        #expect(swingScoredAtText(regradeTestSignal(regrade: queued)) == "Scored Mon 16 Nov, 10:42 ET · stock at $140.00")
        #expect(swingScoredAtText(regradeTestSignal(regrade: done())) == "Scored Mon 16 Nov, 10:42 ET · stock at $140.00")
        let scored: JSON = "2026-11-18T16:00:00+00:00"
        for block in [
            done(["scored_at": scored]),
            done(["scored_at": scored, "rescored": false, "qualifies": false]),
            ["status": "queued", "requested_at": .string(askedAt), "count": 1, "scored_at": scored],
            ["status": "failed", "count": 1, "finished_at": "2026-11-20T15:01:00+00:00", "scored_at": scored],
        ] as [JSON] {
            #expect(swingScoredAtText(regradeTestSignal(regrade: block)) == "Scored Wed 18 Nov, 11:00 ET · stock at $140.00")
        }
        #expect(swingScoredAtText(regradeTestSignal(regrade: done(["scored_at": "2026-11-20T15:01:00+00:00"]), stock: 151.2))
            == "Scored Fri 20 Nov, 10:01 ET · stock at $151.20")
    }

    @Test func theTimeIsNewYorkTimeOnEitherSideOfTheClockChange() {
        #expect(swingScoredAtText(regradeTestSignal(createdAt: "2026-10-30T14:30:00Z", stock: nil)) == "Scored Fri 30 Oct, 10:30 ET")
        #expect(swingScoredAtText(regradeTestSignal(createdAt: "2026-11-02T14:30:00Z", stock: nil)) == "Scored Mon 2 Nov, 09:30 ET")
        #expect(swingScoredAtText(regradeTestSignal(createdAt: "", stock: nil)) == "Score time unknown")
        #expect(swingScoredAtText(regradeTestSignal(regrade: ["scored_at": "junk"], createdAt: "", stock: nil)) == "Score time unknown")
    }

    @Test func aRescoredRegradeShowsBeforeAndAfter() {
        let s = regradeTestSignal(regrade: done(), score: 71, stock: 151.2, strike: 140, premium: 1.45)
        #expect(swingRegradeChanges(s) == [
            SwingRegradeChange(label: "Score", before: "62", after: "71", move: nil),
            SwingRegradeChange(label: "Stock", before: "$140.00", after: "$151.20", move: "+8.0%"),
            SwingRegradeChange(label: "Strike", before: "$130.00", after: "$140.00", move: nil),
            SwingRegradeChange(label: "Premium", before: "$1.30", after: "$1.45", move: nil),
        ])
    }

    @Test func noBeforeAndAfterUnlessDoneAndRescored() {
        #expect(swingRegradeChanges(regradeTestSignal(regrade: done(["rescored": false]))).isEmpty)
        #expect(swingRegradeChanges(regradeTestSignal(regrade: done(["rescored": nil]))).isEmpty)
        #expect(swingRegradeChanges(regradeTestSignal(regrade: queued)).isEmpty)
        #expect(swingRegradeChanges(regradeTestSignal(regrade: ["status": "failed", "rescored": true])).isEmpty)
        #expect(swingRegradeChanges(regradeTestSignal()).isEmpty)
    }

    @Test func missingFiguresReadAsADash() {
        let s = regradeTestSignal(regrade: done(["previous": [:]]), score: 71, stock: nil)
        #expect(swingRegradeChanges(s) == [
            SwingRegradeChange(label: "Score", before: "—", after: "71", move: nil),
            SwingRegradeChange(label: "Stock", before: "—", after: "—", move: nil),
            SwingRegradeChange(label: "Strike", before: "—", after: "$130.00", move: nil),
            SwingRegradeChange(label: "Premium", before: "—", after: "$1.30", move: nil),
        ])
    }

    @Test func thePercentMove() {
        #expect(swingPctMove(100, 108) == "+8.0%")
        #expect(swingPctMove(100, 96.8) == "-3.2%")
        #expect(swingPctMove(100, 100.01) == "0.0%")
        #expect(swingPctMove(100, 99.99) == "0.0%")
        #expect(swingPctMove(nil, 1) == nil)
        #expect(swingPctMove(0, 5) == nil)
        #expect(swingPctMove(100, nil) == nil)
    }

    /// "No longer qualifies" shows whenever `qualifies` is false (later
    /// blocks carry it until a done regrade overwrites it); a failed or
    /// stale regrade's warning shows beside it.
    @Test func theCardsWarnings() {
        let now = asked.addingTimeInterval(60)
        func warnings(_ block: JSON?) -> [String] { swingRegradeWarnings(regradeTestSignal(regrade: block), now: now) }
        let nlq = "No longer qualifies: score 40 is below the review threshold"
        #expect(warnings(done(["qualifies": false, "reason": "score 40 is below the review threshold"])) == [nlq])
        #expect(warnings(done(["qualifies": false, "reason": nil, "rescored": false])) == ["No longer qualifies"])
        #expect(warnings(done()) == [])
        #expect(warnings(done(["qualifies": nil])) == [])
        #expect(warnings(["status": "failed", "error": "no market data returned for APH"]) == ["Regrade failed: no market data returned for APH"])
        #expect(warnings(["status": "failed", "error": nil]) == ["Regrade failed"])
        #expect(warnings(queued) == [])
        #expect(warnings(nil) == [])

        // Carried through a later request or failure.
        let carried: JSONObject = ["qualifies": false, "reason": "score 40 is below the review threshold", "rescored": true]
        var live = carried
        live["status"] = "queued"
        live["requested_at"] = .string(askedAt)
        #expect(warnings(.object(live)) == [nlq])
        var failed = carried
        failed["status"] = "failed"
        failed["error"] = "boom"
        #expect(warnings(.object(failed)) == [nlq, "Regrade failed: boom"])
        #expect(swingRegradeWarnings(regradeTestSignal(regrade: .object(live)), now: asked.addingTimeInterval(301))
            == [nlq, "Regrade failed: the instance did not finish it within 5 minutes. You can regrade again"])
        #expect(swingRegradeWarnings(regradeTestSignal(regrade: queued), now: asked.addingTimeInterval(301))
            == ["Regrade failed: the instance did not finish it within 5 minutes. You can regrade again"])
    }

    /// Approving a wheel card names the regrade count the operator saw.
    @Test func theApprovalCarriesTheRegradeCountOfAWheelCard() {
        #expect(swingExpectRegradeCount(regradeTestSignal(regrade: done(["count": 2])), "approve") == 2)
        #expect(swingExpectRegradeCount(regradeTestSignal(regrade: done(["count": 2])), "approve_half") == 2)
        #expect(swingExpectRegradeCount(regradeTestSignal(), "approve") == 0)
        #expect(swingExpectRegradeCount(regradeTestSignal(regrade: [:]), "approve") == 0)
        #expect(swingExpectRegradeCount(regradeTestSignal(regrade: done(["count": 2])), "reject") == nil)
        #expect(swingExpectRegradeCount(swingTestSignal("s1"), "approve") == nil)
    }

    @Test func errorStatusesMapToPlainMessages() {
        let already = "A regrade of APH is already under way"
        func text(_ error: any Error) -> String { swingActionErrorText(error, alreadyUnderWay: already) }
        #expect(text(ApiError(message: "signal w1 is already being regraded", statusCode: 409)) == already)
        #expect(text(ApiError(message: "Instance i1 is not running, so the regrade could not reach it", statusCode: 503))
            == "The instance is not running: Instance i1 is not running, so the regrade could not reach it")
        #expect(text(ApiError(message: "  ", statusCode: 503)) == "The instance is not running")
        #expect(text(ApiError(message: "signal w1 is approved, not pending", statusCode: 400)) == "signal w1 is approved, not pending")
        #expect(text(ApiError(message: "", statusCode: 500)) == "The server responded with status 500.")
        #expect(text(ApiError(message: "Cannot reach the server. Check your connection.")) == "Cannot reach the server. Check your connection.")
        #expect(text(ApiError(message: "")) == "Something went wrong.")
    }
}

// MARK: - The model

private func start(_ repo: SwingFakeSource, _ clock: SwingTestClock) async -> PendingSignalsModel {
    let model = PendingSignalsModel(instanceId: "i1", source: { repo }, clock: { clock.now }, followSleep: nil)
    await model.build()
    return model
}

private extension PendingSignalsModel {
    var s: PendingSignalsState { state.value! }
    var first: SwingSignal? { state.value?.signals.first }
}

@Suite(.serialized)
struct PendingSignalsRegradeTests {
    let clock = SwingTestClock(swingUTC(2026, 11, 20, 15, 0, 30))

    @Test func aRegradeShowsQueuedAtOnceAndADoubleTapSendsOneRequest() async {
        let repo = SwingFakeSource([regradeTestSignal()])
        repo.regradeAnswer = regradeTestSignal(regrade: queued)
        repo.regradeGate = SwingTestGate()
        let model = await start(repo, clock)
        #expect(!model.regradeLive)
        let first = Task { await model.regrade(regradeTestSignal()) }
        #expect(await eventually { model.s.isRegrading("w1") })
        #expect(await model.regrade(regradeTestSignal()) == nil)
        repo.regradeGate!.open()
        #expect(await first.value == nil)
        #expect(repo.regradeCalls == ["w1"])
        #expect(model.first?.regrade?.status == "queued")
        #expect(!model.s.isRegrading("w1"))
        #expect(model.regradeLive)
    }

    /// A poll already in flight when the POST answered can carry the row
    /// from before it; the queued block stays until the server shows it.
    @Test func anOlderListCannotEraseTheQueuedRegrade() async {
        let repo = SwingFakeSource([regradeTestSignal()])
        repo.regradeAnswer = regradeTestSignal(regrade: queued)
        let model = await start(repo, clock)
        _ = await model.regrade(regradeTestSignal())
        await model.refresh()
        #expect(model.first?.regrade?.status == "queued")
        #expect(model.regradeLive)

        repo.pending = [regradeTestSignal(regrade: done(), score: 71)]
        await model.refresh()
        #expect(model.first?.regrade?.status == "done")
        #expect(model.first?.score == 71)
        #expect(!model.regradeLive)

        // The server has caught up: its rows are taken as they are.
        repo.pending = [regradeTestSignal()]
        await model.refresh()
        #expect(model.first?.regrade == nil)
    }

    /// M2: a list fetched before the POST must not pair the old row's terms
    /// with the answered block's count. The answered row is shown whole.
    @Test func anOlderListShowsTheAnsweredRowWhole() async {
        let repo = SwingFakeSource([regradeTestSignal()])
        let answered = regradeTestSignal(
            regrade: ["status": "queued", "requested_at": .string(askedAt), "count": 1, "rescored": true,
                      "scored_at": "2026-11-20T14:58:00+00:00"],
            score: 48, stock: 151.2, strike: 125, premium: 1.1
        )
        repo.regradeAnswer = answered
        let model = await start(repo, clock)
        _ = await model.regrade(regradeTestSignal())
        await model.refresh()
        #expect(model.first == answered)
        #expect(swingExpectRegradeCount(model.first!, "approve") == 1)
        #expect(model.first?.strike == 125)
    }

    /// M5: a pull-to-refresh rebuild keeps the in-flight regrade (and
    /// decision) guards.
    @Test func pullToRefreshKeepsARegradeInFlightGuarded() async {
        let repo = SwingFakeSource([regradeTestSignal()])
        repo.regradeAnswer = regradeTestSignal(regrade: queued)
        repo.regradeGate = SwingTestGate()
        let model = await start(repo, clock)
        let first = Task { await model.regrade(regradeTestSignal()) }
        #expect(await eventually { model.s.isRegrading("w1") })
        await model.build()
        #expect(model.s.isRegrading("w1"))
        _ = await model.regrade(regradeTestSignal())
        repo.regradeGate!.open()
        _ = await first.value
        #expect(repo.regradeCalls == ["w1"])
        #expect(!model.s.isRegrading("w1"))
    }

    /// M3: a read cut off by the screen going away is not a failure.
    @Test func aCancelledReadIsNotAnError() async {
        let repo = SwingFakeSource([regradeTestSignal(regrade: queued)])
        let model = await start(repo, clock)
        for error in [CancellationError() as any Error, URLError(.cancelled)] {
            repo.listError = error
            await model.refresh()
            #expect(model.s.refreshError == nil)
            #expect(model.s.signals.map(\.id) == ["w1"])
        }
    }

    /// M7: the 4 s poll waits out the background and resumes on foreground.
    @Test func theRegradePollWaitsOutTheBackground() async {
        let manual = ManualClock()
        let lifecycle = AppLifecycle(isForeground: false)
        let repo = SwingFakeSource([regradeTestSignal(regrade: queued)])
        let model = await start(repo, clock)
        let reads = repo.pendingCalls
        let task = Task { await model.pollRegrades(lifecycle: lifecycle, sleep: manual.sleep) }
        await manual.settle()
        await manual.advance(by: .seconds(12))
        #expect(repo.pendingCalls == reads)
        lifecycle.setForeground(true)
        #expect(await eventually { repo.pendingCalls == reads + 1 })
        task.cancel()
        await task.value
    }

    @Test func alreadyUnderWay409SaysSoAndRereadsTheList() async {
        let repo = SwingFakeSource([regradeTestSignal()])
        repo.regradeError = ApiError(message: "signal w1 is already being regraded", statusCode: 409)
        let model = await start(repo, clock)
        repo.pending = [regradeTestSignal(regrade: queued)]
        let message = await model.regrade(regradeTestSignal())
        #expect(message == "A regrade of APH is already under way")
        #expect(model.first?.regrade?.status == "queued")
        #expect(!model.s.isRegrading("w1"))
        #expect(model.regradeLive)
    }

    @Test func notRunning503KeepsTheCardAndAllowsARetry() async {
        let repo = SwingFakeSource([regradeTestSignal()])
        repo.regradeError = ApiError(message: "Instance i1 is not running, so the regrade could not reach it", statusCode: 503)
        let model = await start(repo, clock)
        let message = await model.regrade(regradeTestSignal())
        #expect(message == "The instance is not running: Instance i1 is not running, so the regrade could not reach it")
        #expect(model.s.signals.map(\.id) == ["w1"])
        #expect(model.first?.regrade == nil)
        #expect(!model.s.isRegrading("w1"))

        repo.regradeError = nil
        repo.regradeAnswer = regradeTestSignal(regrade: queued)
        #expect(await model.regrade(regradeTestSignal()) == nil)
        #expect(repo.regradeCalls == ["w1", "w1"])
        #expect(model.regradeLive)
    }

    @Test func aNetworkFailureSaysWhy() async {
        let repo = SwingFakeSource([regradeTestSignal()])
        repo.regradeError = ApiError(message: "Cannot reach the server. Check your connection.")
        let model = await start(repo, clock)
        #expect(await model.regrade(regradeTestSignal()) == "Cannot reach the server. Check your connection.")
        #expect(!model.s.isRegrading("w1"))
    }

    @Test func aLiveRegradeIsNotAskedAgainButAStaleOneIs() async {
        let repo = SwingFakeSource([regradeTestSignal(regrade: queued)])
        let model = await start(repo, clock)
        #expect(model.regradeLive)
        _ = await model.regrade(regradeTestSignal(regrade: queued))
        #expect(repo.regradeCalls.isEmpty)

        clock.now = swingUTC(2026, 11, 20, 15, 5, 1)
        #expect(!model.regradeLive)
        repo.regradeAnswer = regradeTestSignal(regrade: ["status": "queued", "requested_at": "2026-11-20T15:05:01+00:00"])
        _ = await model.regrade(regradeTestSignal(regrade: queued))
        #expect(repo.regradeCalls == ["w1"])
        #expect(model.regradeLive)
    }

    @Test func swingAndUnlistedCardsAreNeverRegraded() async {
        let repo = SwingFakeSource([swingTestSignal("s1")])
        let model = await start(repo, clock)
        _ = await model.regrade(swingTestSignal("s1"))
        _ = await model.regrade(regradeTestSignal("gone"))
        #expect(repo.regradeCalls.isEmpty)
    }

    @Test func theListIsReReadEvery4sWhileARegradeIsLive() async {
        let manual = ManualClock()
        let repo = SwingFakeSource([regradeTestSignal(regrade: queued)])
        let model = await start(repo, clock)
        let reads = repo.pendingCalls
        let task = Task { await model.pollRegrades(sleep: manual.sleep) }
        await manual.settle()
        #expect(manual.requested == [.seconds(4)])
        await manual.advance(by: .seconds(4))
        #expect(await eventually { repo.pendingCalls == reads + 1 })
        #expect(await eventually { manual.requested == [.seconds(4), .seconds(4)] })

        repo.pending = [regradeTestSignal(regrade: done())]
        await manual.advance(by: .seconds(4))
        await task.value
        #expect(repo.pendingCalls == reads + 2)
        #expect(!model.regradeLive)
        #expect(manual.pendingCount == 0)
    }

    @Test func theRegradePollStopsWhenTheScreenGoes() async {
        let manual = ManualClock()
        let repo = SwingFakeSource([regradeTestSignal(regrade: queued)])
        let model = await start(repo, clock)
        let reads = repo.pendingCalls
        let task = Task { await model.pollRegrades(sleep: manual.sleep) }
        await manual.settle()
        task.cancel()
        await task.value
        await manual.advance(by: .seconds(12))
        #expect(repo.pendingCalls == reads)
    }

    @Test func nothingToWatchMeansNoPoll() async {
        let manual = ManualClock()
        let repo = SwingFakeSource([regradeTestSignal(regrade: queued), regradeTestSignal("w2")])
        let model = await start(repo, clock)
        clock.now = swingUTC(2026, 11, 20, 15, 6)
        await model.pollRegrades(sleep: manual.sleep)
        #expect(manual.requested.isEmpty)
    }
}

// MARK: - The approval version check (412)

private let regradedSince = "This card was regraded since you opened it; review the new terms and approve again."

struct SwingRegradeRepositoryTests {
    @Test func anApprovalSendsTheExpectedCountOnlyWhenGiven() async throws {
        let stub = DataStub(json: "")
        let repo = SwingRepository(client: stub.client)
        _ = try await repo.decide("i1", "w1", "approve", expectRegradeCount: 2)
        _ = try await repo.decide("i1", "w1", "approve", expectRegradeCount: 0)
        _ = try await repo.decide("i1", "w1", "reject", reason: "no")
        let calls = stub.requests
        #expect(calls.map(\.path) == Array(repeating: "/instances/i1/swing/signals/w1/decision", count: 3))
        #expect(calls[0].jsonBody == ["decision": "approve", "expect_regrade_count": 2])
        #expect(calls[1].jsonBody == ["decision": "approve", "expect_regrade_count": 0])
        #expect(calls[2].jsonBody == ["decision": "reject", "reason": "no"])
    }

    @Test func aChangedCardAnswers412WithTheServersWords() async {
        let stub = DataStub(status: 412, json: #"{"detail": "This card was regraded since you opened it; review the new terms and approve again."}"#)
        let repo = SwingRepository(client: stub.client)
        do {
            _ = try await repo.decide("i1", "w1", "approve", expectRegradeCount: 1)
            Issue.record("expected a 412")
        } catch let error as ApiError {
            #expect(error.statusCode == 412)
            #expect(error.message == regradedSince)
        } catch {
            Issue.record("unexpected \(error)")
        }
    }

    @Test func theRegradeAndRunRoutes() async throws {
        let stub = DataStub(json: #"{"signal": {"id": "w1", "lane": "wheel", "status": "pending", "regrade": {"status": "queued", "count": 1}}, "command_id": "c1"}"#)
        let repo = SwingRepository(client: stub.client)
        let row = try await repo.regradeSignal("i1", "w1")
        #expect(stub.last?.method == "POST")
        #expect(stub.last?.path == "/instances/i1/swing/signals/w1/regrade")
        #expect(row.id == "w1")
        #expect(row.regrade?.status == "queued")

        stub.respond(json: #"{"run": {"id": "r1", "status": "running", "phase": "queued"}, "in_progress": true, "command_id": "c2"}"#)
        let started = try await repo.startWheelRun("i1")
        #expect(stub.last?.method == "POST")
        #expect(stub.last?.path == "/instances/i1/wheel/scan")
        #expect(started.inProgress)
        #expect(started.run?.phase == "queued")

        stub.respond(json: #"{"run": null, "in_progress": false}"#)
        let status = try await repo.wheelRunStatus("i1")
        #expect(stub.last?.method == "GET")
        #expect(stub.last?.path == "/instances/i1/wheel/scan")
        #expect(status == WheelRunStatus())
    }
}

@Suite(.serialized)
struct WheelApprovalVersionTests {
    let clock = SwingTestClock(swingUTC(2026, 11, 20, 15, 10))

    @Test func approvingAWheelCardSendsTheCountItShows() async {
        let card = regradeTestSignal(regrade: done(["count": 2]))
        let repo = SwingFakeSource([card, regradeTestSignal("w2"), swingTestSignal("s1"), regradeTestSignal("w3")])
        let model = await start(repo, clock)
        _ = await model.decide(card, "approve")
        _ = await model.decide(regradeTestSignal("w2"), "approve")
        _ = await model.decide(swingTestSignal("s1"), "approve")
        _ = await model.decide(regradeTestSignal("w3"), "reject")
        #expect(repo.decideCalls == ["w1:approve", "w2:approve", "s1:approve", "w3:reject"])
        #expect(repo.decideExpectCounts == [2, 0, nil, nil])
    }

    /// 412: the card was regraded since the operator saw it. Nothing was
    /// sent; the card stays and the list is re-read, so a second approval
    /// carries the new count.
    @Test func aRegradedCard412KeepsTheCardAndRereadsIt() async {
        let seen = regradeTestSignal(regrade: done(["count": 1]))
        let repo = SwingFakeSource([seen])
        repo.decideError = ApiError(message: regradedSince, statusCode: 412)
        let model = await start(repo, clock)
        let newer = regradeTestSignal(regrade: done(["count": 2, "finished_at": "2026-11-20T15:09:00+00:00"]), score: 48, strike: 125)
        repo.pending = [newer]
        let result = await model.decide(seen, "approve")
        #expect(result.outcome == .regraded)
        #expect(result.message == regradedSince)
        #expect(model.s.signals.map(\.id) == ["w1"])
        #expect(model.first?.regrade?.count == 2)
        #expect(model.first?.strike == 125)
        #expect(!model.s.isDeciding("w1"))

        repo.decideError = nil
        let again = await model.decide(model.first!, "approve")
        #expect(again.outcome == .recorded)
        #expect(repo.decideExpectCounts == [1, 2])
    }

    @Test func a412WithoutDetailSaysTheCardChanged() async {
        let repo = SwingFakeSource([regradeTestSignal()])
        repo.decideError = ApiError(message: "", statusCode: 412)
        let model = await start(repo, clock)
        repo.pending = [regradeTestSignal(regrade: done(["count": 1]))]
        let result = await model.decide(regradeTestSignal(), "approve")
        #expect(result.outcome == .regraded)
        #expect(result.message == regradedSince)
        #expect(model.s.signals.map(\.id) == ["w1"])
    }

    /// A 412 whose re-read fails (or brings back the same count) must not
    /// call the old terms new. Approving again sends the same count, which
    /// the server refuses again, so nothing stale can trade; once a re-read
    /// lands, the new terms show.
    @Test func a412WhoseReReadFailsDoesNotClaimNewTerms() async {
        let seen = regradeTestSignal(regrade: done(["count": 1]))
        let repo = SwingFakeSource([seen])
        repo.decideError = ApiError(message: regradedSince, statusCode: 412)
        let model = await start(repo, clock)
        repo.listError = ApiError(message: "Cannot reach the server. Check your connection.")
        let result = await model.decide(seen, "approve")
        #expect(result.outcome == .regraded)
        #expect(result.message == kRegradedTermsUnavailable)
        #expect(model.first?.regrade?.count == 1)

        _ = await model.decide(model.first!, "approve")
        #expect(repo.decideExpectCounts == [1, 1])

        repo.listError = nil
        repo.pending = [regradeTestSignal(regrade: done(["count": 2]), strike: 125)]
        let healed = await model.decide(model.first!, "approve")
        #expect(healed.message == regradedSince)
        #expect(model.first?.regrade?.count == 2)
        #expect(model.first?.strike == 125)
    }

    @Test func a412WhoseCardIsGoneDoesNotClaimNewTerms() async {
        let repo = SwingFakeSource([regradeTestSignal()])
        repo.decideError = ApiError(message: regradedSince, statusCode: 412)
        let model = await start(repo, clock)
        repo.pending = []
        let result = await model.decide(regradeTestSignal(), "approve")
        #expect(result.outcome == .regraded)
        #expect(result.message == kRegradedTermsUnavailable)
    }

    /// 409 still means decided elsewhere: the card goes.
    @Test func a409StillMeansDecidedElsewhere() async {
        let repo = SwingFakeSource([regradeTestSignal()])
        repo.decideError = ApiError(message: "signal w1 was decided first by another click; decisions are final", statusCode: 409)
        let model = await start(repo, clock)
        let result = await model.decide(regradeTestSignal(), "approve")
        #expect(result.outcome == .noLongerPending)
        #expect(model.s.signals.isEmpty)
    }

    /// The review sheet goes back to the order on these, and closes on a result otherwise.
    @Test func theReviewReopensOnlyWhenNothingWasDecided() {
        #expect(SwingOrderReviewSheet.reopens(DecisionResult(.regraded, regradedSince)))
        #expect(SwingOrderReviewSheet.reopens(DecisionResult(.ignored, "")))
        #expect(!SwingOrderReviewSheet.reopens(DecisionResult(.recorded, "")))
        #expect(!SwingOrderReviewSheet.reopens(DecisionResult(.failed, "")))
        #expect(!SwingOrderReviewSheet.reopens(DecisionResult(.noLongerPending, "")))
    }
}
