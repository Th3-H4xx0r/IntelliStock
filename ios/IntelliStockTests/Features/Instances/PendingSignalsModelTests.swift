import Foundation
import Testing
@testable import IntelliStock

// Ported from test/features/swing/swing_controller_test.dart.

private func signal(_ id: String) -> SwingSignal {
    swingTestSignal(id, createdAt: "2026-09-24T13:15:0\(id.count)Z")
}

private func start(_ repo: SwingFakeSource, _ clock: SwingTestClock) async -> PendingSignalsModel {
    let model = PendingSignalsModel(instanceId: "i1", source: { repo }, clock: { clock.now })
    await model.build()
    return model
}

private extension PendingSignalsModel {
    var s: PendingSignalsState { state.value! }
}

private func ids(_ rows: [SwingSignal]) -> [String] { rows.map(\.id) }

struct SwingLanesTests {
    @Test func findsLanesByIdOrClassNameIgnoresEverythingElse() {
        #expect(swingLanesOf(["strategies": [["strategy": "strategy_swing"]]]).swing)
        let l = swingLanesOf(["strategies": [["strategy": "StrategyWheel"], ["strategy": "strategy_eb"], "junk"]])
        #expect([l.swing, l.wheel, l.any] == [false, true, true])
        #expect(!swingLanesOf(["strategies": [["strategy": "strategy_eb"]]]).any)
        #expect(!swingLanesOf(nil).any)
        #expect(!swingLanesOf(["strategies": "oops"]).any)
    }
}

@Suite(.serialized)
struct PendingSignalsDecideTests {
    let clock = SwingTestClock(swingUTC(2026, 9, 25, 13, 30))

    @Test func aDoubleTapSendsOneRequest() async {
        let repo = SwingFakeSource([signal("a1")])
        repo.gate = SwingTestGate()
        let model = await start(repo, clock)
        let first = Task { await model.decide(signal("a1"), "approve") }
        #expect(await eventually { model.s.isDeciding("a1") })
        let second = await model.decide(signal("a1"), "approve")
        #expect(second.outcome == .ignored)
        repo.gate!.open()
        #expect(await first.value.outcome == .recorded)
        #expect(repo.decideCalls == ["a1:approve"])
        #expect(model.s.signals.isEmpty)
        #expect(model.s.deciding.isEmpty)
    }

    @Test func decidedElsewhere400RemovesTheCardWithTheServerReason() async {
        let repo = SwingFakeSource([signal("a1"), signal("b22")])
        repo.decideError = ApiError(message: "signal a1 is approved, not pending", statusCode: 400)
        let model = await start(repo, clock)
        let result = await model.decide(signal("a1"), "approve")
        #expect(result.outcome == .noLongerPending)
        #expect(result.message == "signal a1 is approved, not pending")
        #expect(ids(model.s.signals) == ["b22"])
        #expect(model.s.deciding.isEmpty)
    }

    @Test func forbidden403KeepsTheCardAndReEnablesTheButtons() async {
        let repo = SwingFakeSource([signal("a1")])
        repo.decideError = ApiError(message: "not your instance", statusCode: 403)
        let model = await start(repo, clock)
        let result = await model.decide(signal("a1"), "reject")
        #expect(result.outcome == .failed)
        #expect(result.message == "not your instance")
        #expect(ids(model.s.signals) == ["a1"])
        #expect(!model.s.isDeciding("a1"))
    }

    @Test func brokerNotRunning503KeepsTheCardAndAllowsARetry() async {
        let repo = SwingFakeSource([signal("a1")])
        repo.decideError = ApiError(message: "instance i1 is not running", statusCode: 503)
        let model = await start(repo, clock)
        let result = await model.decide(signal("a1"), "approve")
        #expect(result.outcome == .failed)
        #expect(result.message == "instance i1 is not running")
        #expect(ids(model.s.signals) == ["a1"])
        #expect(!model.s.isDeciding("a1"))
        repo.decideError = nil
        let retry = await model.decide(signal("a1"), "approve")
        #expect(retry.outcome == .recorded)
        #expect(repo.decideCalls == ["a1:approve", "a1:approve"])
        #expect(model.s.signals.isEmpty)
    }

    @Test func a202DropsTheCardAndSaysTheOrderMayBeInFlight() async {
        let repo = SwingFakeSource([signal("a1"), signal("b22")])
        repo.decideReceipt = DecisionReceipt(uncertain: true, detail: kUncertainApproval)
        let model = await start(repo, clock)
        let result = await model.decide(signal("a1"), "approve")
        #expect(result.outcome == .uncertain)
        #expect(result.message == kUncertainApproval)
        #expect(ids(model.s.signals) == ["b22"])
        #expect(model.s.deciding.isEmpty)

        repo.decideReceipt = DecisionReceipt(uncertain: true)
        let bare = await model.decide(signal("b22"), "approve")
        #expect(bare.outcome == .uncertain)
        #expect(bare.message == "Approval received, but its delivery to the broker could not be confirmed. Do NOT place this order by hand — it may still be queued. The card will show submitted or failed shortly.")
        for m in [result.message, bare.message] {
            #expect(!m.contains("place the order manually"))
            #expect(!m.contains("no broker command was queued"))
        }
    }

    @Test func a503WithoutDetailSaysNotQueued() async {
        let repo = SwingFakeSource([signal("a1")])
        repo.decideError = ApiError(message: "", statusCode: 503)
        let model = await start(repo, clock)
        let result = await model.decide(signal("a1"), "approve")
        #expect(result.outcome == .failed)
        #expect(result.message == "Not queued — the signal is still pending; try again.")
        #expect(ids(model.s.signals) == ["a1"])
    }

    @Test func expiredSession401KeepsTheCard() async {
        let repo = SwingFakeSource([signal("a1")])
        repo.decideError = ApiError(message: "Not authenticated", statusCode: 401)
        let model = await start(repo, clock)
        let result = await model.decide(signal("a1"), "approve")
        #expect(result.outcome == .failed)
        #expect(result.message == "Session expired — please sign in again.")
        #expect(model.s.signals.count == 1)
    }

    /// Starts a held poll and waits until its pending read has begun.
    private func racingPoll(_ model: PendingSignalsModel, _ repo: SwingGatedSource) async -> (Task<Void, Never>, SwingTestGate) {
        let hold = SwingTestGate()
        repo.nextListGate = hold
        let before = repo.listCalls
        let racing = Task { await model.refresh() }
        _ = await eventually { repo.listCalls > before }
        return (racing, hold)
    }

    @Test func aPollThatRacedARecordedDecisionDoesNotResurrectTheCard() async {
        let repo = SwingGatedSource([signal("a1")])
        repo.listGate.open()
        let model = await start(repo, clock)
        let (racing, hold) = await racingPoll(model, repo)
        _ = await model.decide(signal("a1"), "approve")
        hold.open()
        await racing.value
        #expect(model.s.signals.isEmpty)
    }

    @Test func aPollBegunAfterThe2xxGovernsSoABrokerResetShowsAgain() async {
        let repo = SwingFakeSource([signal("a1")])
        let model = await start(repo, clock)
        _ = await model.decide(signal("a1"), "approve")
        #expect(model.s.signals.isEmpty)
        await model.refresh()
        #expect(ids(model.s.signals) == ["a1"])
        let again = await model.decide(signal("a1"), "approve")
        #expect(again.outcome == .recorded)
        #expect(repo.decideCalls == ["a1:approve", "a1:approve"])
    }

    @Test func pullToRefreshClearsTheLocalHide() async {
        let repo = SwingGatedSource([signal("a1")])
        repo.listGate.open()
        let model = await start(repo, clock)
        let (racing, hold) = await racingPoll(model, repo)
        _ = await model.decide(signal("a1"), "approve")
        hold.open()
        await racing.value
        #expect(model.s.signals.isEmpty)
        await model.build()
        #expect(ids(model.s.signals) == ["a1"])
    }

    @Test func anOlderPollThatLandsAfterANewerOneIsIgnored() async {
        let repo = SwingGatedSource([signal("a1")])
        repo.listGate.open()
        let model = await start(repo, clock)
        let (older, hold) = await racingPoll(model, repo)
        repo.pending = []
        await model.refresh()
        #expect(model.s.signals.isEmpty)
        repo.pending = [signal("a1")]
        hold.open()
        await older.value
        #expect(model.s.signals.isEmpty)
    }

    @Test func aFailedPollKeepsTheLastGoodListAndReportsTheError() async {
        let repo = SwingFakeSource([signal("a1")])
        let model = await start(repo, clock)
        repo.listError = ApiError(message: "Cannot reach the server.")
        await model.refresh()
        #expect(ids(model.s.signals) == ["a1"])
        #expect(model.s.refreshError == "Cannot reach the server.")
    }
}

@Suite(.serialized)
struct PendingSignalsUncertainTests {
    let clock = SwingTestClock(swingUTC(2026, 9, 25, 13, 30))

    private func uncertainOn(_ repo: SwingFakeSource) async -> PendingSignalsModel {
        repo.decideReceipt = DecisionReceipt(uncertain: true, detail: kUncertainApproval)
        let model = await start(repo, clock)
        let result = await model.decide(signal("a1"), "approve")
        #expect(result.outcome == .uncertain)
        return model
    }

    @Test func a202PutsTheSignalOnAWaitingCard() async {
        let repo = SwingFakeSource([signal("a1")])
        let model = await uncertainOn(repo)
        let card = model.s.uncertain.first!
        #expect(model.s.uncertain.count == 1)
        #expect(card.signal.id == "a1")
        #expect(card.badge == "uncertain — waiting for the broker")
        #expect(waitingCopy == "Delivery to the broker could not be confirmed. Do NOT place this order by hand. This should show submitted or failed within a minute; if it is still waiting after 2 minutes you can re-send it here.")
        #expect(model.s.signals.isEmpty)

        repo.pending = []
        repo.approved = [approvedTestSignal("a1", "2026-09-25T13:00:00Z")]
        await model.refresh()
        #expect(model.s.uncertain.first?.resolved == nil)
        #expect(model.s.stuck.isEmpty)
        #expect(repo.statusReads.contains("submitted"))
        #expect(repo.statusReads.contains("failed"))
    }

    @Test func aLaterPollReportingSubmittedSettlesTheBadgeAndDismissRemovesIt() async {
        let repo = SwingFakeSource([signal("a1")])
        let model = await uncertainOn(repo)
        repo.pending = []
        repo.submitted = [withTestStatus(signal("a1"), "submitted", orderClientId: "instance-1-abc-0")]
        await model.refresh()
        #expect(model.s.uncertain.first?.badge == "submitted")
        model.dismissUncertain("a1")
        #expect(model.s.uncertain.isEmpty)
    }

    @Test func aSubmittedRowWithoutAnOrderKeyKeepsWaitingAndCanReturnToPending() async {
        let repo = SwingFakeSource([signal("a1")])
        let model = await uncertainOn(repo)
        repo.pending = []
        repo.submitted = [withTestStatus(signal("a1"), "submitted")]
        await model.refresh()
        #expect(model.s.uncertain.first?.resolved == nil)
        #expect(model.s.uncertain.first?.badge == "uncertain — waiting for the broker")
        repo.submitted = []
        repo.pending = [signal("a1")]
        await model.refresh()
        #expect(model.s.uncertain.isEmpty)
        #expect(ids(model.s.signals) == ["a1"])
    }

    @Test func aSubmittedRowWithoutAnOrderKeyThenSweptFailedSaysFailed() async {
        let repo = SwingFakeSource([signal("a1")])
        let model = await uncertainOn(repo)
        repo.pending = []
        repo.submitted = [withTestStatus(signal("a1"), "submitted", orderClientId: "")]
        await model.refresh()
        #expect(model.s.uncertain.first?.resolved == nil)
        repo.submitted = []
        repo.failed = [withTestStatus(signal("a1"), "failed")]
        await model.refresh()
        #expect(model.s.uncertain.first?.badge == "failed")
    }

    @Test func aLaterPollReportingFailedSaysFailed() async {
        let repo = SwingFakeSource([signal("a1")])
        let model = await uncertainOn(repo)
        repo.pending = []
        repo.failed = [withTestStatus(signal("a1"), "failed")]
        await model.refresh()
        #expect(model.s.uncertain.first?.badge == "failed")
    }

    @Test func aLaterPollReportingPendingEndsTheWaitingCard() async {
        let repo = SwingFakeSource([signal("a1")])
        let model = await uncertainOn(repo)
        await model.refresh()
        #expect(model.s.uncertain.isEmpty)
        #expect(ids(model.s.signals) == ["a1"])
    }

    @Test func aPollThatBeganBeforeThe202CannotSettleIt() async {
        let repo = SwingGatedSource([signal("a1")])
        repo.listGate.open()
        repo.decideReceipt = DecisionReceipt(uncertain: true, detail: kUncertainApproval)
        let model = await start(repo, clock)
        let hold = SwingTestGate()
        repo.nextListGate = hold
        let racing = Task { await model.refresh() }
        _ = await eventually { repo.listCalls == 2 }
        _ = await model.decide(signal("a1"), "approve")
        hold.open()
        await racing.value
        #expect(model.s.uncertain.first?.resolved == nil)
        #expect(model.s.signals.isEmpty)
    }

    @Test func a202ReSendWaitsOnACardToo() async {
        let repo = SwingFakeSource([], approved: [approvedTestSignal("a1", "2026-09-25T13:20:00Z")])
        repo.resendReceipt = DecisionReceipt(uncertain: true)
        let model = await start(repo, clock)
        let result = await model.resend(model.s.stuck[0])
        #expect(result.outcome == .uncertain)
        #expect(model.s.stuck.isEmpty)
        #expect(result.message == uncertainMessage("Re-send"))
        #expect(model.s.uncertain.first?.signal.id == "a1")
    }
}

@Suite(.serialized)
struct PendingSignalsRound4Tests {
    private func joined(_ clock: SwingTestClock) async -> (PendingSignalsModel, SwingFakeSource) {
        // The device runs 90 s behind the server: decided_at is 13:31:30.
        let repo = SwingFakeSource([signal("a1")])
        repo.decideReceipt = DecisionReceipt(uncertain: true)
        let model = await start(repo, clock)
        _ = await model.decide(signal("a1"), "approve")
        repo.pending = []
        repo.approved = [approvedTestSignal("a1", "2026-09-25T13:31:30Z")]
        return (model, repo)
    }

    @Test func aCardThatJoinedTheStuckListStaysThereUnderClockSkew() async {
        let clock = SwingTestClock(swingUTC(2026, 9, 25, 13, 30))
        let (model, repo) = await joined(clock)
        let start0 = clock.now
        var seen: [String] = []
        for s in [121.0, 151, 181, 211] {
            clock.now = start0.addingTimeInterval(s)
            await model.refresh()
            seen.append(ids(model.s.stuck).joined(separator: ","))
        }
        #expect(seen == ["a1", "a1", "a1", "a1"])
        repo.approved = []
        await model.refresh()
        #expect(model.s.stuck.isEmpty)
        repo.approved = [approvedTestSignal("a1", "2026-09-25T13:31:30Z")]
        clock.now = start0.addingTimeInterval(200)
        await model.refresh()
        #expect(model.s.stuck.isEmpty)
    }

    @Test func aJoinedCardStillWaitsOutAReSend() async {
        let clock = SwingTestClock(swingUTC(2026, 9, 25, 13, 30))
        let (model, _) = await joined(clock)
        clock.now = clock.now.addingTimeInterval(121)
        await model.refresh()
        _ = await model.resend(model.s.stuck[0])
        clock.now = clock.now.addingTimeInterval(60)
        await model.refresh()
        #expect(model.s.stuck.isEmpty)
        clock.now = clock.now.addingTimeInterval(61)
        await model.refresh()
        #expect(ids(model.s.stuck) == ["a1"])
    }

    @Test func aNewDecisionOnADismissedSignalForgetsTheDismissal() async {
        let clock = SwingTestClock(swingUTC(2026, 9, 25, 13, 30))
        let repo = SwingFakeSource([], approved: [approvedTestSignal("a1", "2026-09-25T13:20:00Z")])
        let model = await start(repo, clock)
        model.dismissStuck("a1")
        repo.approved = []
        repo.pending = [signal("a1")]
        repo.decideReceipt = DecisionReceipt(uncertain: true)
        clock.now = clock.now.addingTimeInterval(30)
        await model.refresh()
        _ = await model.decide(signal("a1"), "approve")
        repo.pending = []
        repo.approved = [approvedTestSignal("a1", "2026-09-25T13:30:30Z")]
        clock.now = clock.now.addingTimeInterval(121)
        await model.refresh()
        #expect(model.s.uncertain.isEmpty)
        #expect(ids(model.s.stuck) == ["a1"])
    }

    @Test func stillApprovedTwoMinutesAfterThe202ItJoinsTheStuckList() async {
        let clock = SwingTestClock(swingUTC(2026, 9, 25, 13, 30))
        let repo = SwingFakeSource([signal("a1")])
        repo.decideReceipt = DecisionReceipt(uncertain: true)
        let model = await start(repo, clock)
        _ = await model.decide(signal("a1"), "approve")
        repo.pending = []
        repo.approved = [approvedTestSignal("a1", "2026-09-25T13:30:30Z")]
        clock.now = clock.now.addingTimeInterval(stuckAfter)
        await model.refresh()
        #expect(model.s.uncertain.first?.resolved == nil) // exactly 2 min: waiting
        #expect(model.s.stuck.isEmpty)
        clock.now = clock.now.addingTimeInterval(1)
        await model.refresh()
        #expect(model.s.uncertain.isEmpty)
        #expect(ids(model.s.stuck) == ["a1"])
    }

    @Test func aFailedApprovedReadCannotMoveItButDismissIsOfferedAfterTwoMinutes() async {
        let clock = SwingTestClock(swingUTC(2026, 9, 25, 13, 30))
        let repo = SwingFakeSource([signal("a1")])
        repo.decideReceipt = DecisionReceipt(uncertain: true)
        let model = await start(repo, clock)
        _ = await model.decide(signal("a1"), "approve")
        let card = model.s.uncertain[0]
        #expect(!card.canDismiss(clock.now.addingTimeInterval(60)))
        #expect(card.canDismiss(clock.now.addingTimeInterval(stuckAfter + 1)))
        repo.pending = []
        repo.approvedError = ApiError(message: "down")
        clock.now = clock.now.addingTimeInterval(180)
        await model.refresh()
        #expect(model.s.uncertain.first?.resolved == nil)
        model.dismissUncertain("a1")
        #expect(model.s.uncertain.isEmpty)
    }

    @Test func aDismissedStuckCardStaysOffAcrossPolls() async {
        let clock = SwingTestClock(swingUTC(2026, 9, 25, 13, 30))
        let repo = SwingFakeSource([], approved: [approvedTestSignal("a1", "2026-09-25T13:20:00Z")])
        let model = await start(repo, clock)
        #expect(ids(model.s.stuck) == ["a1"])
        model.dismissStuck("a1")
        #expect(model.s.stuck.isEmpty)
        await model.refresh()
        #expect(model.s.stuck.isEmpty)
    }
}

@Suite(.serialized)
struct PendingSignalsReadsTests {
    let clock = SwingTestClock(swingUTC(2026, 9, 25, 13, 30))

    @Test func aFailedApprovedReadNeverStopsThePendingListRefreshing() async {
        let repo = SwingFakeSource([signal("a1")], approved: [approvedTestSignal("old", "2026-09-25T13:20:00Z")])
        let model = await start(repo, clock)
        #expect(ids(model.s.stuck) == ["old"])
        repo.pending = [signal("a1"), signal("b22")]
        repo.approvedError = ApiError(message: "Could not load signals (502)")
        await model.refresh()
        #expect(ids(model.s.signals) == ["a1", "b22"])
        #expect(ids(model.s.stuck) == ["old"])
        #expect(model.s.refreshError == "Could not load signals (502)")
    }

    @Test func anApprovedReadThatFailsOnTheFirstLoadStillShowsThePendingList() async {
        let repo = SwingFakeSource([signal("a1")])
        repo.approvedError = ApiError(message: "Could not load signals (502)")
        let model = await start(repo, clock)
        #expect(ids(model.s.signals) == ["a1"])
        #expect(model.s.refreshError == "Could not load signals (502)")
    }

    @Test func aFailedPendingReadKeepsTheLastGoodListWhileApprovedRefreshes() async {
        let repo = SwingFakeSource([signal("a1")])
        let model = await start(repo, clock)
        repo.approved = [approvedTestSignal("old", "2026-09-25T13:20:00Z")]
        repo.pendingError = ApiError(message: "pending down")
        await model.refresh()
        repo.pendingError = nil
        #expect(ids(model.s.signals) == ["a1"])
        #expect(ids(model.s.stuck) == ["old"])
        #expect(model.s.refreshError == "pending down")
    }

    @Test func noPendingListOnTheFirstLoadIsAnError() async {
        let repo = SwingFakeSource([])
        repo.listError = ApiError(message: "Cannot reach the server.")
        let model = await start(repo, clock)
        #expect(model.state.errorMessage == "Cannot reach the server.")
    }
}

@Suite(.serialized)
struct PendingSignalsStuckTests {
    @Test func nyDateIsTheNewYorkCalendarDateAcrossDST() {
        #expect(nyDate(swingUTC(2026, 9, 25, 1, 30)) == "2026-09-24")
        #expect(nyDate(swingUTC(2026, 9, 25, 4, 0)) == "2026-09-25")
        #expect(nyDate(swingUTC(2026, 3, 8, 4, 59)) == "2026-03-07")
        #expect(nyDate(swingUTC(2026, 3, 8, 7, 0)) == "2026-03-08")
        #expect(nyDate(swingUTC(2026, 11, 1, 4, 30)) == "2026-11-01")
        #expect(nyDate(swingUTC(2026, 11, 2, 4, 30)) == "2026-11-01")
        #expect(nyDate(swingUTC(2026, 1, 1, 4, 59)) == "2025-12-31")
    }

    @Test func resendBlockedReasonReadsWhenItWasApprovedInNewYork() {
        #expect(resendBlockedReason(approvedTestSignal("a", "2026-09-25T14:00:00Z", session: "2026-09-21"), "2026-09-25") == nil)
        #expect(resendBlockedReason(approvedTestSignal("a", "2026-09-24T15:00:00Z", session: "2026-09-25"), "2026-09-25")
            == "This approval was made on 2026-09-24; approve a fresh signal instead.")
        #expect(resendBlockedReason(approvedTestSignal("a", "2026-09-25T01:30:00Z"), "2026-09-24") == nil)
        #expect(resendBlockedReason(approvedTestSignal("a", "nope"), "2026-09-25")
            == "This approval was made on an unknown date; approve a fresh signal instead.")
    }

    @Test func anApprovalUnclaimedForMoreThanTwoMinutesIsOfferedAReSend() async {
        let clock = SwingTestClock(swingUTC(2026, 9, 25, 13, 30))
        let repo = SwingFakeSource([], approved: [
            approvedTestSignal("old", "2026-09-25T13:27:59Z"),
            approvedTestSignal("edge", "2026-09-25T13:28:00Z"),
            approvedTestSignal("new", "2026-09-25T13:29:30Z", status: "approved_half"),
        ])
        let model = await start(repo, clock)
        #expect(ids(model.s.stuck) == ["old"])
        clock.now = clock.now.addingTimeInterval(31)
        await model.refresh()
        #expect(ids(model.s.stuck) == ["old", "edge"])
        #expect(model.s.signals.isEmpty)
    }

    @Test func aReSendQueuesOnceThenWaitsAnotherTwoMinutes() async {
        let clock = SwingTestClock(swingUTC(2026, 9, 25, 13, 30))
        let repo = SwingFakeSource([], approved: [approvedTestSignal("a1", "2026-09-25T13:20:00Z")])
        let model = await start(repo, clock)
        let result = await model.resend(model.s.stuck[0])
        #expect(result.outcome == .recorded)
        #expect(result.message == "Re-sent AAPL to the broker.")
        #expect(repo.resendCalls == ["a1"])
        #expect(model.s.stuck.isEmpty)
        await model.refresh()
        #expect(model.s.stuck.isEmpty)
        clock.now = clock.now.addingTimeInterval(121)
        await model.refresh()
        #expect(ids(model.s.stuck) == ["a1"])
    }

    @Test func aDoubleTapOnReSendSendsOneRequest() async {
        let clock = SwingTestClock(swingUTC(2026, 9, 25, 13, 30))
        let repo = SwingFakeSource([], approved: [approvedTestSignal("a1", "2026-09-25T13:20:00Z")])
        repo.gate = SwingTestGate()
        let model = await start(repo, clock)
        let s = model.s.stuck[0]
        let first = Task { await model.resend(s) }
        #expect(await eventually { model.s.isResending("a1") })
        #expect(await model.resend(s).outcome == .ignored)
        repo.gate!.open()
        _ = await first.value
        #expect(repo.resendCalls == ["a1"])
    }

    @Test func a409DropsTheCardAndSnoozesIt() async {
        let clock = SwingTestClock(swingUTC(2026, 9, 25, 13, 30))
        let repo = SwingFakeSource([], approved: [approvedTestSignal("a1", "2026-09-25T13:20:00Z")])
        repo.resendError = ApiError(message: "command c1 for signal a1 is still pending; ...", statusCode: 409)
        let model = await start(repo, clock)
        let result = await model.resend(model.s.stuck[0])
        #expect(result.outcome == .noLongerPending)
        #expect(result.message.contains("still pending"))
        await model.refresh()
        #expect(model.s.stuck.isEmpty)
    }

    @Test func a503KeepsTheCardAndSaysNotQueued() async {
        let clock = SwingTestClock(swingUTC(2026, 9, 25, 13, 30))
        let repo = SwingFakeSource([], approved: [approvedTestSignal("a1", "2026-09-25T13:20:00Z")])
        repo.resendError = ApiError(message: "", statusCode: 503)
        let model = await start(repo, clock)
        let result = await model.resend(model.s.stuck[0])
        #expect(result.outcome == .failed)
        #expect(result.message == "Not queued — try again.")
        #expect(ids(model.s.stuck) == ["a1"])
        #expect(!model.s.isResending("a1"))
    }

    @Test func a202ReSendIsUncertainAndCarriesTheServerAdvice() async {
        let clock = SwingTestClock(swingUTC(2026, 9, 25, 13, 30))
        let repo = SwingFakeSource([], approved: [approvedTestSignal("a1", "2026-09-25T13:20:00Z")])
        repo.resendReceipt = DecisionReceipt(uncertain: true)
        let model = await start(repo, clock)
        let result = await model.resend(model.s.stuck[0])
        #expect(result.outcome == .uncertain)
        #expect(result.message == "Re-send received, but its delivery to the broker could not be confirmed. Do NOT place this order by hand — it may still be queued. The card will show submitted or failed shortly.")
        #expect(model.s.stuck.isEmpty)
    }

    @Test func aRacingPollThatAlsoSeesTheIdApprovedNeverShowsItPending() async {
        let clock = SwingTestClock(swingUTC(2026, 9, 25, 13, 30))
        let repo = SwingGatedSource([signal("a1")])
        repo.listGate.open()
        let model = await start(repo, clock)
        let hold = SwingTestGate()
        repo.nextListGate = hold
        let racing = Task { await model.refresh() }
        _ = await eventually { repo.listCalls == 2 }
        _ = await model.decide(signal("a1"), "approve")
        repo.pending = []
        repo.approved = [approvedTestSignal("a1", "2026-09-25T13:29:59Z")]
        hold.open()
        await racing.value
        #expect(model.s.signals.isEmpty)
        #expect(model.s.stuck.isEmpty)
        repo.pending = [signal("a1")]
        repo.approved = []
        await model.refresh()
        #expect(ids(model.s.signals) == ["a1"])
    }

    @Test func leftDuringTheFirstFetchNoPollerOutlivesTheScreen() async {
        let manual = ManualClock()
        let repo = SwingGatedSource([signal("a1")])
        let model = PendingSignalsModel(instanceId: "i1", source: { repo }, clock: { Date() })
        let task = Task { await model.poll(lifecycle: nil, sleep: manual.sleep) }
        #expect(await eventually { repo.listCalls == 1 })
        task.cancel()
        repo.listGate.open()
        await task.value
        await manual.advance(by: .seconds(90))
        #expect(repo.listCalls == 1)
        #expect(manual.pendingCount == 0)
    }
}

struct SwingCopyTests {
    @Test func copyNamesTheSymbolAndTheDecision() {
        let s = signal("a1")
        #expect(decisionLabel("approve_half") == "Approve ½")
        #expect(decisionConfirmBody(s, "reject").contains("final"))
        #expect(decisionSuccessMessage(s, "approve").hasPrefix("Approved AAPL"))
    }

    @Test func approvalCopySaysTheBrokerRebuildsAndChecksNeverThatItPlaced() {
        let s = signal("a1")
        #expect(decisionConfirmBody(s, "approve") == "Approve AAPL? The broker rebuilds the order at the live price and checks it before sending. Approving sends the order. If the broker can't place it yet (e.g. before the open) the signal returns here to approve again.")
        #expect(decisionConfirmBody(s, "approve_half") == "Approve AAPL at half size? The broker rebuilds the order at the live price and checks it before sending. Approving sends the order. If the broker can't place it yet (e.g. before the open) the signal returns here to approve again.")
        #expect(!decisionConfirmBody(s, "approve").contains("final"))
        #expect(decisionSuccessMessage(s, "approve") == "Approved AAPL. The broker rebuilds and checks the order at the live price before sending it.")
        #expect(decisionSuccessMessage(s, "approve_half") == "Approved AAPL at half size. The broker rebuilds and checks the order at the live price before sending it.")
        #expect(decisionConfirmBody(s, "reject") == "Reject AAPL? Decisions are final.")
        #expect(decisionSuccessMessage(s, "reject") == "Rejected AAPL.")
        let promise = try! NSRegularExpression(pattern: "placed|goes out|within seconds|command poll|notif")
        for d in ["approve", "approve_half"] {
            for text in [decisionConfirmBody(s, d), decisionSuccessMessage(s, d)] {
                #expect(promise.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) == nil)
            }
        }
    }

    @Test func stuckLabelsCountWholeMinutesAtLeastOne() {
        let now = swingUTC(2026, 9, 25, 13, 30)
        #expect(stuckLabel(approvedTestSignal("a", "2026-09-25T13:25:00Z"), now) == "Approved 5 min ago; the broker has not picked it up yet.")
        #expect(stuckLabel(approvedTestSignal("a", "2026-09-25T13:29:30Z"), now) == "Approved 1 min ago; the broker has not picked it up yet.")
        #expect(stuckLabel(approvedTestSignal("a", "x"), now) == "Approved; the broker has not picked it up yet.")
    }
}
