import Foundation
import Testing
@testable import IntelliStock

/// Records what the manual order model sends, and can hold a send open.
@MainActor
private final class LiveOrderSendRecorder {
    private(set) var sent: [JSONObject] = []
    var hold = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func send(_ payload: JSONObject) async {
        sent.append(payload)
        if hold { await withCheckedContinuation { waiters.append($0) } }
    }

    func release() {
        hold = false
        waiters.forEach { $0.resume() }
        waiters = []
    }
}

/// Live Trading's manual order: nothing is sent until "Submit order?" is
/// confirmed, validation runs first, and a confirmed order goes out once.
struct LiveManualOrderModelTests {
    private func makeModel(_ recorder: LiveOrderSendRecorder) -> LiveManualOrderModel {
        LiveManualOrderModel { await recorder.send($0) }
    }

    @Test func submitAsksForConfirmationAndSendsNothing() {
        let recorder = LiveOrderSendRecorder()
        let model = makeModel(recorder)
        model.form = OrderForm(symbol: "aapl", side: "buy", orderType: "limit", qty: "10", limitPrice: "185", tif: "day")
        model.requestSubmit()
        #expect(model.confirmation?.summary == "Buy 10 AAPL · Limit $185.00 · Day")
        #expect(model.error == nil)
        #expect(recorder.sent.isEmpty)
    }

    @Test func anInvalidFormNeverReachesTheConfirmation() {
        let recorder = LiveOrderSendRecorder()
        let model = makeModel(recorder)
        model.form = OrderForm(symbol: "AAPL", orderType: "limit", qty: "5")
        model.requestSubmit()
        #expect(model.error == "Limit order requires a positive limit price.")
        #expect(model.confirmation == nil)
        #expect(recorder.sent.isEmpty)
    }

    @Test func cancelSendsNothing() async {
        let recorder = LiveOrderSendRecorder()
        let model = makeModel(recorder)
        model.form = OrderForm(symbol: "AAPL", qty: "1")
        model.requestSubmit()
        model.cancelConfirmation()
        #expect(model.confirmation == nil)
        // Nothing confirmed: a stray Submit sends nothing either.
        #expect(await model.confirmSubmit() == false)
        #expect(recorder.sent.isEmpty)
    }

    @Test func submitSendsTheExistingPayloadOnce() async {
        let recorder = LiveOrderSendRecorder()
        let model = makeModel(recorder)
        let form = OrderForm(symbol: "tsla", side: "sell", orderType: "market", notional: "1000", tif: "gtc")
        model.form = form
        model.requestSubmit()
        #expect(model.confirmation?.summary == "Sell $1,000.00 of TSLA · Market · GTC")
        #expect(await model.confirmSubmit())
        #expect(recorder.sent == [buildOrderPayload(form)])
        #expect(model.confirmation == nil)
    }

    @Test func aSecondConfirmWhileInFlightSendsNothing() async {
        let recorder = LiveOrderSendRecorder()
        recorder.hold = true
        let model = makeModel(recorder)
        model.form = OrderForm(symbol: "AAPL", qty: "2")
        model.requestSubmit()
        let first = Task { await model.confirmSubmit() }
        #expect(await eventually { model.submitting })
        // The guard: a second tap, or a new Submit Order, does nothing.
        #expect(await model.confirmSubmit() == false)
        model.requestSubmit()
        #expect(model.confirmation == nil)
        recorder.release()
        #expect(await first.value)
        #expect(recorder.sent.count == 1)
    }

    @Test func theSummaryNamesEveryFieldThatIsSent() {
        #expect(liveOrderSummary(buildOrderPayload(OrderForm(symbol: "SPY", side: "buy", orderType: "market", qty: "0.5", tif: "ioc")))
            == "Buy 0.5 SPY · Market · IOC")
        #expect(liveOrderSummary(buildOrderPayload(OrderForm(symbol: "MSFT", side: "sell", orderType: "limit", qty: "1250", limitPrice: "410.5", tif: "day", extendedHours: true)))
            == "Sell 1,250 MSFT · Limit $410.50 · Day · Extended hours")
    }
}

/// End to end: the confirmed order reaches `POST /instances/{id}/live-command`
/// as `submit_order` with the form's payload, and only then.
struct LiveManualOrderWireTests {
    @Test func onlyTheConfirmedOrderIsPosted() async {
        let stub = DataStub()
        stub.handler = { req in
            req.path == "/instances/i1/live-command"
                ? (200, #"{"command_id": "c1", "status": "completed"}"#)
                : (200, #"{"status": "active"}"#)
        }
        let clock = ManualClock()
        let live = LiveTradingModel(instanceId: "i1", repository: { LiveRepository(client: stub.client) }, sleep: clock.sleep)
        await live.load()
        let order = LiveManualOrderModel { await live.runCommand("submit_order", $0) }
        order.form = OrderForm(symbol: "AAPL", qty: "3")

        order.requestSubmit()
        #expect(!stub.requests.contains { $0.path == "/instances/i1/live-command" })

        #expect(await order.confirmSubmit())
        let posts = stub.requests.filter { $0.path == "/instances/i1/live-command" }
        #expect(posts.count == 1)
        #expect(posts.first?.method == "POST")
        #expect(posts.first?.jsonBody == ["type": "submit_order", "payload": .object(buildOrderPayload(OrderForm(symbol: "AAPL", qty: "3")))])
    }
}
