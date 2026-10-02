import Foundation
import Testing
@testable import IntelliStock

// Swing-approvals fix, 2026-10-02. On swing-paper every approval of QCOM and
// PSKY was claimed by the broker, refused by the order gate
// ("dependency.watchdog.unhealthy,dependency.watchdog.stale — approve
// again") and put back to pending within a second. The app showed nothing:
// the card went on Approve and silently came back on the next poll. These
// tests pin the fix: an approval is followed through its live command, and
// the server's reason lands on the card.

private let watchdogRefusal = "order gate blocked: dependency.watchdog.unhealthy,dependency.watchdog.stale — approve again"
private let noContract = "swing approval failed: ValueError: No put contracts found for UHS exp 2026-10-09"

private extension PendingSignalsModel {
    var s: PendingSignalsState { state.value! }
}

private func ids(_ rows: [SwingSignal]) -> [String] { rows.map(\.id) }

@Suite(.serialized)
struct SwingApprovalOutcomeTests {
    let clock = SwingTestClock(swingUTC(2026, 10, 2, 18, 39, 50))

    private func start(_ repo: SwingFakeSource) async -> PendingSignalsModel {
        let model = PendingSignalsModel(instanceId: "swing-paper", source: { repo }, clock: { clock.now }, followSleep: nil)
        await model.build()
        return model
    }

    private func approved(_ repo: SwingFakeSource, _ signal: SwingSignal, command: String = "c1") async -> PendingSignalsModel {
        repo.decideReceipt = DecisionReceipt(commandId: command)
        let model = await start(repo)
        let result = await model.decide(signal, "approve")
        #expect(result.outcome == .recorded)
        return model
    }

    @Test func anApprovalWithACommandIsFollowedWhileTheBrokerWorks() async {
        let qcom = returnedTestSignal("q1")
        let repo = SwingFakeSource([qcom])
        let model = await approved(repo, qcom)
        #expect(model.s.signals.isEmpty)
        #expect(model.s.tracks.map(\.id) == ["q1"])
        #expect(model.s.tracks.first?.phase == .sending)
        #expect(model.s.tracks.first?.commandId == "c1")
        #expect(model.s.refusals.isEmpty)
    }

    @Test func aRefusedApprovalComesBackWithTheServersReason() async {
        let qcom = returnedTestSignal("q1")
        let repo = SwingFakeSource([qcom])
        let model = await approved(repo, qcom)
        repo.commandStatuses["c1"] = swingTestCommand("c1", "failed", error: watchdogRefusal)
        await model.refresh()
        #expect(ids(model.s.signals) == ["q1"])
        #expect(model.s.tracks.isEmpty)
        #expect(model.s.refusals["q1"]?.message == watchdogRefusal)
        #expect(model.s.refusals["q1"]?.at == clock.now)
        #expect(repo.commandReads == ["c1"])

        // Settled: later polls keep the reason and stop reading the command.
        await model.refresh()
        #expect(model.s.refusals["q1"]?.message == watchdogRefusal)
        #expect(repo.commandReads == ["c1"])
    }

    @Test func aRefusalReadBeforeThePendingRowWaitsOnItsOwnCard() async {
        let qcom = returnedTestSignal("q1")
        let repo = SwingFakeSource([qcom])
        let model = await approved(repo, qcom)
        // The command read answered failed, but this poll's pending read was
        // served before the broker's reset landed.
        repo.pending = []
        repo.commandStatuses["c1"] = swingTestCommand("c1", "failed", error: watchdogRefusal)
        await model.refresh()
        #expect(model.s.signals.isEmpty)
        #expect(model.s.tracks.first?.phase == .refused)
        #expect(model.s.tracks.first?.message == watchdogRefusal)
        #expect(model.s.refusals.isEmpty)

        repo.pending = [qcom]
        await model.refresh()
        #expect(ids(model.s.signals) == ["q1"])
        #expect(model.s.tracks.isEmpty)
        #expect(model.s.refusals["q1"]?.message == watchdogRefusal)
    }

    @Test func aDefinitelyFailedApprovalStaysUntilDismissed() async {
        let uhs = returnedTestSignal("u1", symbol: "UHS")
        let repo = SwingFakeSource([uhs])
        let model = await approved(repo, uhs)
        repo.pending = []
        repo.commandStatuses["c1"] = swingTestCommand("c1", "failed", error: noContract)
        await model.refresh()
        await model.refresh()
        #expect(model.s.tracks.map(\.phase) == [.refused])
        #expect(model.s.tracks.first?.message == noContract)
        model.dismissTrack("u1")
        #expect(model.s.tracks.isEmpty)
        await model.refresh()
        #expect(model.s.tracks.isEmpty)
    }

    @Test func aSentApprovalSaysSoUntilDismissed() async {
        let psky = returnedTestSignal("p1", symbol: "PSKY")
        let repo = SwingFakeSource([psky])
        let model = await approved(repo, psky, command: "c7")
        repo.pending = []
        repo.commandStatuses["c7"] = swingTestCommand("c7", "completed")
        await model.refresh()
        #expect(model.s.tracks.map(\.phase) == [.sent])
        #expect(model.s.signals.isEmpty)
        model.dismissTrack("p1")
        #expect(model.s.tracks.isEmpty)
    }

    @Test func aQueuedOrUnreadableCommandKeepsSending() async {
        let qcom = returnedTestSignal("q1")
        let repo = SwingFakeSource([qcom])
        let model = await approved(repo, qcom)
        repo.commandStatuses["c1"] = swingTestCommand("c1", "running")
        await model.refresh()
        #expect(model.s.tracks.map(\.phase) == [.sending])
        repo.commandError = ApiError(message: "bad gateway", statusCode: 502)
        await model.refresh()
        #expect(model.s.tracks.map(\.phase) == [.sending])
        // A failed command read is not a failed poll.
        #expect(model.s.refreshError == nil)
        // The hide ended with the first poll after the 2xx: the row the
        // server still lists pending shows again, as before this fix.
        #expect(ids(model.s.signals) == ["q1"])
    }

    @Test func approvingAgainClearsTheOldReason() async {
        let qcom = returnedTestSignal("q1")
        let repo = SwingFakeSource([qcom])
        let model = await approved(repo, qcom)
        repo.commandStatuses["c1"] = swingTestCommand("c1", "failed", error: watchdogRefusal)
        await model.refresh()
        #expect(model.s.refusals["q1"] != nil)
        repo.decideReceipt = DecisionReceipt(commandId: "c2")
        let again = await model.decide(qcom, "approve")
        #expect(again.outcome == .recorded)
        #expect(model.s.refusals.isEmpty)
        #expect(model.s.tracks.map(\.commandId) == ["c2"])
    }

    @Test func aRefusalIsDroppedOnceTheSignalIsNoLongerPending() async {
        let qcom = returnedTestSignal("q1")
        let repo = SwingFakeSource([qcom])
        let model = await approved(repo, qcom)
        repo.commandStatuses["c1"] = swingTestCommand("c1", "failed", error: watchdogRefusal)
        await model.refresh()
        repo.pending = [] // rejected on the web, say
        await model.refresh()
        #expect(model.s.refusals.isEmpty)
    }

    @Test func aRejectionIsNotFollowed() async {
        let qcom = returnedTestSignal("q1")
        let repo = SwingFakeSource([qcom])
        let model = await start(repo)
        let result = await model.decide(qcom, "reject")
        #expect(result.outcome == .recorded)
        #expect(model.s.tracks.isEmpty)
        #expect(repo.commandReads.isEmpty)
    }

    @Test func aReSendIsFollowedToo() async {
        let stuck = approvedTestSignal("a1", "2026-10-02T18:30:00Z")
        let repo = SwingFakeSource([], approved: [stuck])
        repo.resendReceipt = DecisionReceipt(commandId: "c9")
        let model = await start(repo)
        #expect(ids(model.s.stuck) == ["a1"])
        let result = await model.resend(stuck)
        #expect(result.outcome == .recorded)
        #expect(model.s.tracks.map(\.commandId) == ["c9"])
        #expect(model.s.stuck.isEmpty)
    }

    @Test func aTrackedApprovalNeverAlsoShowsAsStuck() async {
        let qcom = returnedTestSignal("q1")
        let repo = SwingFakeSource([qcom])
        let model = await approved(repo, qcom)
        // Unclaimed for more than two minutes: the stuck card (with Re-send)
        // takes over from the waiting one.
        repo.pending = []
        repo.approved = [approvedTestSignal("q1", "2026-10-02T18:39:50Z", symbol: "QCOM")]
        clock.now = clock.now.addingTimeInterval(180)
        await model.refresh()
        #expect(ids(model.s.stuck) == ["q1"])
        #expect(model.s.tracks.isEmpty)
    }

    private func following(_ repo: SwingFakeSource, _ sleeps: SwingSleepLog) async -> PendingSignalsModel {
        let model = PendingSignalsModel(
            instanceId: "swing-paper", source: { repo }, clock: { clock.now },
            followSleep: { duration in sleeps.record(duration) }
        )
        await model.build()
        return model
    }

    @Test func theFollowUpPollsSoonAfterTheApprovalAndStopsOnceSettled() async {
        let qcom = returnedTestSignal("q1")
        let repo = SwingFakeSource([qcom])
        repo.decideReceipt = DecisionReceipt(commandId: "c1")
        repo.commandStatuses["c1"] = swingTestCommand("c1", "failed", error: watchdogRefusal)
        let sleeps = SwingSleepLog()
        let model = await following(repo, sleeps)
        _ = await model.decide(qcom, "approve")
        // Seconds, not the 30 s poll: the reason is on the card at once.
        #expect(await eventually { model.s.refusals["q1"]?.message == watchdogRefusal })
        for _ in 0..<50 { await Task.yield() }
        #expect(sleeps.durations == [PendingSignalsModel.followDelays[0]])
        #expect(repo.commandReads == ["c1"])
    }

    @Test func theFollowUpGivesUpAfterItsLastDelay() async {
        let qcom = returnedTestSignal("q1")
        let repo = SwingFakeSource([qcom])
        repo.decideReceipt = DecisionReceipt(commandId: "c1")
        repo.commandStatuses["c1"] = swingTestCommand("c1", "pending")
        let sleeps = SwingSleepLog()
        let model = await following(repo, sleeps)
        _ = await model.decide(qcom, "approve")
        #expect(await eventually { sleeps.durations.count == PendingSignalsModel.followDelays.count })
        #expect(await eventually { repo.commandReads.count == PendingSignalsModel.followDelays.count })
        for _ in 0..<50 { await Task.yield() }
        #expect(sleeps.durations == PendingSignalsModel.followDelays)
        // The 30 s poll keeps reading it after that.
        #expect(model.s.tracks.map(\.phase) == [.sending])
    }
}

/// Records the follow-up's sleeps and returns at once.
nonisolated final class SwingSleepLog: @unchecked Sendable {
    private let lock = NSLock()
    private var _durations: [Duration] = []
    var durations: [Duration] { lock.withLock { _durations } }

    func record(_ duration: Duration) {
        lock.withLock { _durations.append(duration) }
    }
}

struct SwingApprovalPresentationTests {
    @Test func theWatchdogRefusalSaysWhatToDo() {
        let text = swingRefusalText(watchdogRefusal)
        #expect(text.headline == "The order gate's safety watchdog has stopped reporting, so the broker refuses every new order. Approving again won't help until it reports again; restarting the instance restarts it.")
        #expect(text.detail == watchdogRefusal)
    }

    @Test func otherRefusalsReadPlainly() {
        #expect(swingRefusalText("order gate blocked: option.collateral_insufficient").headline
            == "Not enough cash to secure this put.")
        #expect(swingRefusalText("order gate blocked: option.underlying_cap").headline
            == "This put would tie up more of the account in one stock than the lane allows.")
        #expect(swingRefusalText("order gate blocked: market.regular_hours_required — approve again after the open").headline
            == "The market is closed. Approve again during regular hours.")
        #expect(swingRefusalText("after the close — approve again after 20:00 ET or pre-market").headline
            == "The market is closed. Approve again during regular hours.")
        #expect(swingRefusalText("no live price for QCOM — approve again after the open").headline
            == "There was no fresh quote. Approve again once the market is open.")
        #expect(swingRefusalText(noContract).headline
            == "No put contract matched this strike and expiry.")
        #expect(swingRefusalText("approval from 2026-09-30 — approve a fresh signal").headline
            == "This approval is from an earlier day. Approve a fresh signal.")
        let other = swingRefusalText("order gate blocked: exposure.max_order_notional")
        #expect(other.headline == "The broker did not send it.")
        #expect(other.detail == "order gate blocked: exposure.max_order_notional")
        #expect(swingRefusalText("").detail == "")
    }

    @Test func aReturnedSignalSaysTheBrokerSentNothing() {
        var utc = Calendar(identifier: .gregorian)
        utc.timeZone = TimeZone(secondsFromGMT: 0)!
        let note = swingReturnedNote(returnedTestSignal("q1"), calendar: utc)
        #expect(note == "You approved this before, but the broker sent no order and returned it on Oct 2 at 18:39. The live log below says why.")
        #expect(swingReturnedNote(swingTestSignal("a1")) == nil)
    }

    @Test func collateralAboveTheCashIsFlagged() {
        let qcom = returnedTestSignal("q1") // strike 184.15 x 100 = 18,415
        #expect(swingCollateralWarning(qcom, cash: 17_836)
            == "Collateral $18,415.00 is more than the account's $17,836.00 cash. The broker picks the strike again when you approve and refuses the order if the cash can't secure it.")
        #expect(swingCollateralWarning(qcom, cash: 20_000) == nil)
        #expect(swingCollateralWarning(qcom, cash: nil) == nil)
        #expect(swingCollateralWarning(swingTestSignal("a1"), cash: 1) == nil)
    }

    @Test func thePutSellersFiguresLeadTheWheelCard() {
        let qcom = returnedTestSignal("q1") // premium 2.26 a share, 1 contract
        let lead = swingWheelLead(qcom)
        #expect(lead.premium == "$226.00")
        #expect(lead.premiumFootnote == "$2.26 a share")
        #expect(lead.collateral == "$18,415.00")
        #expect(lead.collateralFootnote == "1.23% return")
        #expect(swingWheelDetails(qcom).map(\.label) == ["Strike", "Expiry", "Contracts", "Contract", "Limit"])
        #expect(swingWheelDetails(qcom).map(\.value) == ["$184.15", "2026-10-09", "1", "Picked at approval", "Set at approval"])
        let aph = wheelTestSignal("w1")
        #expect(swingWheelDetails(aph).map(\.value) == ["$130.00", "2026-10-02", "1", "APH261002P00130000", "$1.23"])
        #expect(swingScoreLabel(70) == "Score 70")
        #expect(swingScoreLabel(nil) == "No score")
        #expect(swingLaneLabel(qcom) == "Wheel")
        #expect(swingLaneLabel(swingTestSignal("a1")) == "Swing")
    }

    @Test func aScanRowLinksToItsSignal() {
        let scan = WheelScan(json: ["id": "s1", "session": "2026-09-28", "symbol": "WDAY", "strike": 184.13,
                                    "expiry": "2026-10-09", "status": "pending"])
        let rejected = SwingSignal(json: ["id": "w1", "lane": "wheel", "symbol": "WDAY", "session": "2026-09-28",
                                          "status": "rejected", "proposal": ["strike": 184.13]])
        let otherStrike = SwingSignal(json: ["id": "w2", "lane": "wheel", "symbol": "WDAY", "session": "2026-09-28",
                                             "status": "pending", "proposal": ["strike": 180]])
        let swingLane = SwingSignal(json: ["id": "w3", "lane": "swing", "symbol": "WDAY", "session": "2026-09-28",
                                           "status": "pending"])
        #expect(wheelScanSignal(scan, in: [otherStrike, swingLane, rejected])?.id == "w1")
        #expect(wheelScanSignal(scan, in: [otherStrike, swingLane]) == nil)

        // The scan log still says pending; the signal was rejected.
        #expect(wheelScanStatus(scan, signal: rejected, pendingIds: []) == WheelScanStatus(label: "Rejected", tone: .bad, reviewable: false))
        let pending = SwingSignal(json: ["id": "w1", "lane": "wheel", "symbol": "WDAY", "session": "2026-09-28",
                                         "status": "pending", "proposal": ["strike": 184.13]])
        #expect(wheelScanStatus(scan, signal: pending, pendingIds: ["w1"]) == WheelScanStatus(label: "Pending", tone: .waiting, reviewable: true))
        // Listed pending a moment ago but gone from the live queue: decided.
        #expect(wheelScanStatus(scan, signal: pending, pendingIds: []) == WheelScanStatus(label: "Decided", tone: .neutral, reviewable: false))
        #expect(wheelScanStatus(scan, signal: nil, pendingIds: []) == WheelScanStatus(label: "Pending", tone: .waiting, reviewable: false))
        let skipped = WheelScan(json: ["id": "s2", "symbol": "X", "status": "skipped", "skip_reason": "earnings"])
        #expect(wheelScanStatus(skipped, signal: nil, pendingIds: []) == WheelScanStatus(label: "Skipped", tone: .neutral, reviewable: false))
        let auto = SwingSignal(json: ["id": "w9", "lane": "wheel", "status": "auto_approved"])
        #expect(wheelScanStatus(scan, signal: auto, pendingIds: []).label == "Auto-approved")
        let failed = SwingSignal(json: ["id": "w8", "lane": "wheel", "status": "failed"])
        #expect(wheelScanStatus(scan, signal: failed, pendingIds: []) == WheelScanStatus(label: "Failed", tone: .bad, reviewable: false))
        let submitted = SwingSignal(json: ["id": "w7", "lane": "wheel", "status": "submitted"])
        #expect(wheelScanStatus(scan, signal: submitted, pendingIds: []).tone == .good)
    }

    @Test func theWheelModelLoadsTheSignalsTheScansLinkTo() async {
        let repo = SwingFakeSource([])
        repo.recent = [SwingSignal(json: ["id": "w1", "lane": "wheel", "status": "rejected"])]
        let model = WheelModel(instanceId: "i1", source: { repo })
        await model.load()
        #expect(model.signals.map(\.id) == ["w1"])
        #expect(repo.recentCalls == 1)
        // A failed signal read never fails the book.
        repo.recentError = ApiError(message: "nope", statusCode: 500)
        await model.load()
        #expect(model.state.value != nil)
        #expect(model.signals.map(\.id) == ["w1"])
    }
}
