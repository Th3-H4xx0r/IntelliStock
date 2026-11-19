import Foundation
import Testing
@testable import IntelliStock

// The manual "Run wheel now" (backend swing_trader/wheel_manual.py): the
// run row, its progress and summary lines, and WheelModel's start and 4 s
// poll. A manual run only creates review cards; it never places an order.

// MARK: - Decoding

struct WheelRunDecodingTests {
    @Test func aRunRowDecodes() throws {
        let status = WheelRunStatus(json: [
            "run": [
                "id": "r1", "instance_id": "i1", "status": "running", "phase": "scoring", "requested_by": "pranav",
                "started_at": "2026-11-20T15:00:00+00:00", "heartbeat_at": "2026-11-20T15:02:00+00:00",
                "finished_at": nil, "session": "2026-11-20", "scanned": 503, "passed_screen": 41,
                "to_score": 30, "scored": 12, "new_pending": 3, "rejected": 8, "skipped_existing": 2,
                "errors": 1, "error": nil,
            ],
            "in_progress": true,
            "command_id": "c1",
        ])
        #expect(status.inProgress)
        let run = try #require(status.run)
        #expect(run.id == "r1")
        #expect(run.status == "running")
        #expect(run.phase == "scoring")
        #expect(run.requestedBy == "pranav")
        #expect(run.startedAt == swingUTC(2026, 11, 20, 15, 0))
        #expect(run.finishedAt == nil)
        #expect(run.session == "2026-11-20")
        #expect([run.scanned, run.passedScreen, run.toScore, run.scored] as [Int] == [503, 41, 30, 12])
        #expect([run.newPending, run.rejected, run.skippedExisting, run.errors] as [Int] == [3, 8, 2, 1])
        #expect(run.error == "")
    }

    @Test func noRunYetOrJunkReadsAsIdle() throws {
        let none = WheelRunStatus(json: ["run": nil, "in_progress": false])
        #expect(none.run == nil)
        #expect(!none.inProgress)
        #expect(WheelRunStatus(json: .null) == WheelRunStatus())
        let junk = WheelRunStatus(json: ["run": "x", "in_progress": "yes"])
        #expect(junk.run == nil)
        #expect(!junk.inProgress)

        let sparse = try #require(WheelRunStatus(json: ["run": ["status": "done", "scored": "12", "error": "  boom "]]).run)
        #expect(sparse.scored == 12)
        #expect(sparse.scanned == 0)
        #expect(sparse.phase == "")
        #expect(sparse.error == "boom")
    }
}

// MARK: - Presentation

struct WheelRunPresentationTests {
    private func run(_ phase: String, status: String = "running", _ fields: JSONObject = [:]) -> WheelRun {
        wheelRunTestStatus(phase, inProgress: status == "running", status: status, fields).run!
    }

    @Test func progressNamesThePhaseAndItsCounts() {
        #expect(wheelRunProgress(run("queued")) == "Waiting for the instance to start the run…")
        #expect(wheelRunProgress(run("screening")) == "Screening the wheel universe…")
        #expect(wheelRunProgress(run("earnings", ["scanned": 503, "passed_screen": 41]))
            == "Checking earnings dates · 41 of 503 passed the screen")
        #expect(wheelRunProgress(run("scoring", ["scored": 12, "to_score": 30, "new_pending": 3]))
            == "Scoring 12 of 30 · 3 new cards so far")
        #expect(wheelRunProgress(run("scoring", ["scored": 1, "to_score": 30, "new_pending": 1]))
            == "Scoring 1 of 30 · 1 new card so far")
        #expect(wheelRunProgress(run("scoring", ["to_score": 30])) == "Scoring 0 of 30 · no new cards yet")
        #expect(wheelRunProgress(run("")) == "Starting the run…")
    }

    @Test func aFinishedRunSummarisesEveryCount() {
        let done = run("done", status: "done", [
            "scanned": 503, "passed_screen": 41, "scored": 30, "new_pending": 3, "rejected": 25,
            "skipped_existing": 2, "errors": 0,
        ])
        #expect(wheelRunSummary(done)
            == "Manual run 20 Nov: scanned 503, passed screen 41, scored 30, 3 new cards, 25 rejected, 2 already had a card, 0 errors")
        let one = run("done", status: "done", ["scanned": 503, "passed_screen": 1, "scored": 1, "new_pending": 1, "errors": 1])
        #expect(wheelRunSummary(one)
            == "Manual run 20 Nov: scanned 503, passed screen 1, scored 1, 1 new card, 0 rejected, 0 already had a card, 1 error")
    }

    @Test func aFailedOrAbandonedRunSaysSo() {
        #expect(wheelRunSummary(run("failed", status: "failed", ["error": "RuntimeError: no conviction model is linked to the wheel lane"]))
            == "Manual run 20 Nov failed: RuntimeError: no conviction model is linked to the wheel lane")
        #expect(wheelRunSummary(run("failed", status: "failed")) == "Manual run 20 Nov failed")
        // The server reads a run whose heartbeat stopped as not in progress.
        #expect(wheelRunSummary(run("scoring", status: "running"))
            == "Manual run 20 Nov stopped before it finished. You can run it again")
        #expect(wheelRunSummary(run("", status: "")) == "")
    }

    @Test func theRunsDayFallsBackToItsStart() {
        let started = run("failed", status: "failed", ["session": nil, "started_at": "2026-11-21T02:00:00+00:00"])
        #expect(wheelRunSummary(started) == "Manual run 20 Nov failed")
        let undated = run("failed", status: "failed", ["session": nil, "started_at": nil])
        #expect(wheelRunSummary(undated) == "Manual run failed")
    }

    @Test func theConfirmationSaysItPlacesNoOrders() {
        #expect(wheelRunConfirmMessage.contains("Places no orders"))
        #expect(wheelRunConfirmMessage.contains("AI"))
    }
}

// MARK: - The model

@Suite(.serialized)
struct WheelRunModelTests {
    private func make(_ repo: SwingFakeSource) -> WheelModel {
        WheelModel(instanceId: "i1", source: { repo })
    }

    @Test func openingReadsTheLastRunOnce() async {
        let repo = SwingFakeSource([])
        repo.runStatuses = [wheelRunTestStatus("done", inProgress: false, status: "done")]
        let model = make(repo)
        #expect(model.run == nil)
        await model.loadRun()
        #expect(repo.runStatusCalls == 1)
        #expect(model.run?.run?.status == "done")
        #expect(!model.runInProgress)
        #expect(model.runsFinished == 0)
    }

    @Test func openingResumesARunInProgress() async {
        let repo = SwingFakeSource([])
        repo.runStatuses = [wheelRunTestStatus("scoring", inProgress: true)]
        let model = make(repo)
        await model.loadRun()
        #expect(model.runInProgress)
        #expect(model.run?.run?.phase == "scoring")
    }

    @Test func aStartedRunIsPolledEvery4sUntilItFinishes() async {
        let manual = ManualClock()
        let repo = SwingFakeSource([])
        repo.startRunAnswer = wheelRunTestStatus("queued", inProgress: true)
        repo.runStatuses = [
            wheelRunTestStatus("scoring", inProgress: true),
            wheelRunTestStatus("done", inProgress: false, status: "done"),
        ]
        let model = make(repo)
        await model.startRun()
        #expect(repo.startRunCalls == 1)
        #expect(model.runInProgress)
        #expect(model.run?.run?.phase == "queued")
        #expect(model.runError == nil)

        let task = Task { await model.pollRun(sleep: manual.sleep) }
        await manual.settle()
        #expect(manual.requested == [.seconds(4)])
        await manual.advance(by: .seconds(4))
        #expect(await eventually { model.run?.run?.phase == "scoring" })
        await manual.advance(by: .seconds(4))
        await task.value
        #expect(!model.runInProgress)
        #expect(model.run?.run?.status == "done")
        #expect(model.runsFinished == 1)
        #expect(repo.runStatusCalls == 2)
    }

    @Test func aSecondTapWhileStartingOrRunningSendsNothing() async {
        let repo = SwingFakeSource([])
        repo.startRunAnswer = wheelRunTestStatus("queued", inProgress: true)
        repo.startRunGate = SwingTestGate()
        let model = make(repo)
        let first = Task { await model.startRun() }
        #expect(await eventually { model.runStarting })
        await model.startRun()
        repo.startRunGate!.open()
        await first.value
        #expect(!model.runStarting)
        await model.startRun()
        #expect(repo.startRunCalls == 1)
    }

    @Test func alreadyUnderWay409ShowsTheRunThatIsGoing() async {
        let repo = SwingFakeSource([])
        repo.startRunError = ApiError(message: "a wheel run is already under way for i1", statusCode: 409)
        repo.runStatuses = [wheelRunTestStatus("scoring", inProgress: true)]
        let model = make(repo)
        await model.startRun()
        #expect(model.runError == "A wheel run is already under way")
        #expect(repo.runStatusCalls == 1)
        #expect(model.runInProgress)
        #expect(!model.runStarting)
    }

    @Test func notRunning503SaysSoAndLeavesTheButtonOn() async {
        let repo = SwingFakeSource([])
        repo.startRunError = ApiError(message: "Instance i1 is not running, so the wheel run could not reach it", statusCode: 503)
        let model = make(repo)
        await model.startRun()
        #expect(model.runError == "The instance is not running: Instance i1 is not running, so the wheel run could not reach it")
        #expect(!model.runInProgress)
        #expect(!model.runStarting)

        // A new start clears the last error.
        repo.startRunError = nil
        repo.startRunAnswer = wheelRunTestStatus("queued", inProgress: true)
        await model.startRun()
        #expect(model.runError == nil)
        #expect(model.runInProgress)
    }

    @Test func aFailedReadKeepsTheLastRunAndTheNextReadRecovers() async {
        let repo = SwingFakeSource([])
        repo.runStatuses = [wheelRunTestStatus("scoring", inProgress: true)]
        let model = make(repo)
        await model.loadRun()
        repo.runStatusError = ApiError(message: "Cannot reach the server. Check your connection.")
        await model.loadRun()
        #expect(model.runInProgress)
        #expect(model.run?.run?.phase == "scoring")
        #expect(model.runReadError == "Cannot reach the server. Check your connection.")
        repo.runStatusError = nil
        await model.loadRun()
        #expect(model.runReadError == nil)
    }

    /// M1: a status read sent before a start cannot undo the started run.
    @Test func anOlderReadCannotUndoAStartedRun() async {
        let repo = SwingFakeSource([])
        repo.runStatuses = [wheelRunTestStatus("done", inProgress: false, status: "done")]
        let gate = SwingTestGate()
        repo.runStatusGate = gate
        let model = make(repo)
        let read = Task { await model.loadRun() }
        #expect(await eventually { repo.runStatusCalls == 1 })
        repo.startRunAnswer = wheelRunTestStatus("queued", inProgress: true)
        await model.startRun()
        gate.open()
        await read.value
        #expect(model.runInProgress)
        #expect(model.run?.run?.phase == "queued")
        #expect(model.runsFinished == 0)
    }

    /// F1: a status read sent while the start is in flight, and answered
    /// before the start's 200 (the run row did not exist yet), cannot drop
    /// that 200: the start is numbered when its answer arrives.
    @Test func aReadAnsweredDuringTheStartCannotDropItsAnswer() async {
        let repo = SwingFakeSource([])
        repo.runStatuses = [wheelRunTestStatus("done", inProgress: false, status: "done")]
        repo.startRunAnswer = wheelRunTestStatus("queued", inProgress: true)
        let gate = SwingTestGate()
        repo.startRunGate = gate
        let model = make(repo)
        let start = Task { await model.startRun() }
        #expect(await eventually { repo.startRunCalls == 1 })
        await model.loadRun()
        #expect(model.run?.run?.status == "done")
        gate.open()
        await start.value
        #expect(model.runInProgress)
        #expect(model.run?.run?.phase == "queued")
        #expect(model.runsFinished == 0)
    }

    /// F2: a started run does not show an old read error.
    @Test func aStartedRunClearsAnOldReadError() async {
        let repo = SwingFakeSource([])
        repo.runStatusError = ApiError(message: "Cannot reach the server. Check your connection.")
        let model = make(repo)
        await model.loadRun()
        #expect(model.runReadError == "Cannot reach the server. Check your connection.")
        repo.startRunAnswer = wheelRunTestStatus("queued", inProgress: true)
        await model.startRun()
        #expect(model.runInProgress)
        #expect(model.runReadError == nil)
    }

    /// M1: of two overlapping reads, the older answer never wins.
    @Test func overlappingReadsKeepTheNewestAnswer() async {
        let repo = SwingFakeSource([])
        repo.runStatuses = [wheelRunTestStatus("scoring", inProgress: true)]
        let model = make(repo)
        await model.loadRun()
        let gate = SwingTestGate()
        repo.runStatusGate = gate
        let older = Task { await model.loadRun() }
        #expect(await eventually { repo.runStatusCalls == 2 })
        repo.runStatuses = [wheelRunTestStatus("done", inProgress: false, status: "done")]
        await model.loadRun()
        #expect(model.runsFinished == 1)
        gate.open()
        await older.value
        #expect(!model.runInProgress)
        #expect(model.run?.run?.status == "done")
        #expect(model.runsFinished == 1)
    }

    /// M7: the run poll waits out the background and resumes on foreground.
    @Test func theRunPollWaitsOutTheBackground() async {
        let manual = ManualClock()
        let lifecycle = AppLifecycle(isForeground: false)
        let repo = SwingFakeSource([])
        repo.runStatuses = [wheelRunTestStatus("scoring", inProgress: true)]
        let model = make(repo)
        await model.loadRun()
        let task = Task { await model.pollRun(lifecycle: lifecycle, sleep: manual.sleep) }
        await manual.settle()
        await manual.advance(by: .seconds(12))
        #expect(repo.runStatusCalls == 1)
        lifecycle.setForeground(true)
        #expect(await eventually { repo.runStatusCalls == 2 })
        task.cancel()
        await task.value
    }

    @Test func theRunPollStopsWhenTheScreenGoes() async {
        let manual = ManualClock()
        let repo = SwingFakeSource([])
        repo.runStatuses = [wheelRunTestStatus("scoring", inProgress: true)]
        let model = make(repo)
        await model.loadRun()
        let task = Task { await model.pollRun(sleep: manual.sleep) }
        await manual.settle()
        task.cancel()
        await task.value
        await manual.advance(by: .seconds(12))
        #expect(repo.runStatusCalls == 1)
    }

    @Test func noRunInProgressMeansNoPoll() async {
        let manual = ManualClock()
        let repo = SwingFakeSource([])
        let model = make(repo)
        await model.loadRun()
        await model.pollRun(sleep: manual.sleep)
        #expect(manual.requested.isEmpty)
    }
}
