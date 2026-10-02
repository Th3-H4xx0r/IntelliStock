import Foundation
import Testing
@testable import IntelliStock

// Ported from test/features/live_trading/{range_stats,manual_order_validation,
// equity_chart_scrub,position_card,trade_row}_test.dart, plus the
// LiveStateNotifier behaviour.

private func history(_ values: [Double]) -> PortfolioHistory {
    let start = DartDateTime.tryParse("2026-06-10T09:30:00")!
    return PortfolioHistory(timestamps: values.indices.map { start.addingTimeInterval(Double($0) * 60) }, values: values)
}

struct LiveRangeStatsTests {
    @Test func emptyHistoryIsZerosUpWithoutHighLow() {
        let s = RangeStats.from(nil)
        #expect(s.dollars == 0 && s.pct == 0 && s.isUp && s.high == nil && s.low == nil)
        #expect(RangeStats.from(history([1000])).high == nil)
    }

    @Test func upAndDownTrends() {
        let up = RangeStats.from(history([100, 110, 115, 120]))
        #expect(close(up.dollars, 20, 0.001) && close(up.pct, 20, 0.001) && up.isUp)
        #expect(up.high == 120 && up.low == 100)
        let down = RangeStats.from(history([200, 190, 180, 175]))
        #expect(close(down.dollars, -25, 0.001) && !down.isUp)
        let flat = RangeStats.from(history([500, 500, 500]))
        #expect(flat.dollars == 0 && flat.isUp)
    }

    @Test func highLowAndPct() {
        let s = RangeStats.from(history([100, 250, 150, 180]))
        #expect(s.high == 250 && s.low == 100)
        #expect(close(RangeStats.from(history([200, 220])).pct, 10, 0.001))
        #expect(RangeStats.from(history([0, 100])).pct == 0)
    }
}

struct LiveOrderFormTests {
    @Test func symbolAndQtyNotionalXor() {
        #expect(validateOrderForm(OrderForm(symbol: "", qty: "1"))?.contains("Symbol") == true)
        #expect(validateOrderForm(OrderForm(symbol: "AAPL"))?.contains("qty or notional") == true)
        #expect(validateOrderForm(OrderForm(symbol: "AAPL", qty: "10", notional: "500"))?.contains("not both") == true)
        #expect(validateOrderForm(OrderForm(symbol: "AAPL", qty: "10")) == nil)
        #expect(validateOrderForm(OrderForm(symbol: "AAPL", notional: "500")) == nil)
    }

    @Test func positiveNumbersOnly() {
        #expect(validateOrderForm(OrderForm(symbol: "AAPL", qty: "-5"))?.contains("positive") == true)
        #expect(validateOrderForm(OrderForm(symbol: "AAPL", qty: "abc")) == "Qty must be a positive number.")
        #expect(validateOrderForm(OrderForm(symbol: "AAPL", notional: "0")) == "Notional must be a positive number.")
    }

    @Test func limitOrdersNeedAPositiveLimitPrice() {
        #expect(validateOrderForm(OrderForm(symbol: "AAPL", orderType: "limit", qty: "5"))?.contains("limit price") == true)
        #expect(validateOrderForm(OrderForm(symbol: "AAPL", orderType: "limit", qty: "5", limitPrice: "150.00")) == nil)
    }

    @Test func extendedHoursNeedsLimitAndDay() {
        #expect(validateOrderForm(OrderForm(symbol: "AAPL", orderType: "market", qty: "5", extendedHours: true))?.contains("Extended hours") == true)
        #expect(validateOrderForm(OrderForm(symbol: "AAPL", orderType: "limit", qty: "5", limitPrice: "150", tif: "day", extendedHours: true)) == nil)
        #expect(validateOrderForm(OrderForm(symbol: "AAPL", orderType: "limit", qty: "5", limitPrice: "150", tif: "gtc", extendedHours: true))?.contains("day") == true)
    }

    @Test func changingTypeOrTifClearsExtendedHours() {
        var f = OrderForm(symbol: "AAPL", orderType: "limit", tif: "day", extendedHours: true)
        #expect(f.extendedHoursAllowed)
        f.setTif("gtc")
        #expect(!f.extendedHours && !f.extendedHoursAllowed)
        f.setTif("day")
        f.extendedHours = true
        f.setOrderType("market")
        #expect(!f.extendedHours)
    }

    @Test func payloads() throws {
        let qty = buildOrderPayload(OrderForm(symbol: "AAPL", side: "buy", orderType: "market", qty: "10.5", tif: "day"))
        #expect(qty["symbol"] == "AAPL" && qty["side"] == "buy" && qty["qty"] == .double(10.5))
        #expect(qty["notional"] == nil)
        let notional = buildOrderPayload(OrderForm(symbol: "TSLA", side: "sell", orderType: "market", notional: "1000", tif: "gtc"))
        #expect(notional["notional"] == .double(1000) && notional["qty"] == nil)
        let limit = buildOrderPayload(OrderForm(symbol: "SPY", orderType: "limit", qty: "2", limitPrice: "450.50"))
        #expect(limit["limit_price"] == .double(450.5))
        #expect(buildOrderPayload(OrderForm(symbol: "msft", qty: "1"))["symbol"] == "MSFT")
        // Dart's jsonEncode of double.parse("2") wrote 2.0; so does ours.
        let encoded = try JSON.object(buildOrderPayload(OrderForm(symbol: "a", qty: "2"))).dartEncoded()
        #expect(encoded == #"{"symbol":"A","side":"buy","order_type":"market","tif":"day","extended_hours":false,"qty":2.0}"#)
    }

    @Test func haltReasonFallsBackWhenBlank() {
        #expect(liveHaltReason("  ") == "manual halt via UI")
        #expect(liveHaltReason(" risk breach ") == "risk breach")
    }
}

struct LiveEquityGeometryTests {
    /// equity_chart_scrub_test: the left edge reports the first point and the
    /// right edge the last, on the 1D minute axis.
    @Test func scrubMapsLeftEdgeToFirstAndRightEdgeToLast() {
        let start = DartDateTime.localCalendar.startOfDay(for: DartDateTime.tryParse("2026-01-01")!)
        let h = PortfolioHistory(
            timestamps: (0..<11).map { start.addingTimeInterval(Double($0 * 143) * 60) },
            values: (0..<11).map(Double.init)
        )
        let xs = LiveEquityGeometry.xs(h, range: "1D", style: .line)
        #expect(LiveEquityGeometry.scrubIndex(fraction: 0.01, xs: xs, timeAxis: true) <= 1)
        #expect(LiveEquityGeometry.scrubIndex(fraction: 0.99, xs: xs, timeAxis: true) >= 9)
        #expect(LiveEquityGeometry.labels(h, range: "1D", style: .area) == ["12AM", "8AM", "4PM", "12AM"])
    }

    @Test func candlesUseTheIndexAxis() {
        let h = history([1, 2, 3, 4, 5])
        #expect(!LiveEquityGeometry.usesTimeAxis(range: "1D", style: .candle))
        #expect(LiveEquityGeometry.xs(h, range: "1D", style: .candle) == [0, 1, 2, 3, 4])
        #expect(LiveEquityGeometry.scrubIndex(fraction: 0.6, xs: [0, 1, 2, 3, 4], timeAxis: false) == 2)
        #expect(LiveEquityGeometry.hairlineFraction(2, xs: [0, 1, 2, 3, 4], timeAxis: false) == 0.5)
    }

    @Test func bucketsCandles() {
        #expect(liveBucketCandles([1], count: 40).isEmpty)
        let c = liveBucketCandles([1, 5, 2, 8, 3, 9, 4], count: 3)
        // ceil(7/3) = 3 per candle → [1,5,2], [8,3,9], [4]
        #expect(c == [
            LiveCandle(x: 0, open: 1, high: 5, low: 1, close: 2),
            LiveCandle(x: 1, open: 8, high: 9, low: 3, close: 9),
            LiveCandle(x: 2, open: 4, high: 4, low: 4, close: 4),
        ])
        #expect(liveBucketCandles(Array(repeating: 1, count: 10), count: 40).count == 10)
    }

    @Test func rangeLabels() {
        #expect(liveRangeLabel("1D") == "today")
        #expect(liveRangeLabel("3M") == "past 3 months")
        #expect(liveRangeLabel("ALL") == "all time")
    }
}

struct LivePositionCardLogicTests {
    @Test func shortPutWithNoQuoteReadsContractsAndCannotClose() {
        let p = Position(json: [
            "symbol": "APH261002P00130000", "qty": -1, "avg_entry_price": 1.23,
            "last_price": nil, "market_value": nil, "unrealized_pnl": nil, "unrealized_pnl_pct": nil,
            "asset_class": "us_option", "side": "short", "multiplier": 100,
            "underlying": "APH", "strike": 130, "expiry": "2026-10-02",
        ])
        #expect(p.isOption && p.isShort && !p.canClose)
        #expect(p.optionDescription == "APH $130 Put · 2026-10-02")
        #expect(p.quantityLabel == "CONTRACTS")
        #expect(p.quantityText == "1")
        #expect(fmtMoney(p.marketValue) == "—")
        #expect(fmtMoney(p.lastPrice) == "—")
    }

    @Test func stockPositionKeepsSharesAndClose() {
        let p = Position(json: [
            "symbol": "AAPL", "qty": 12, "avg_entry_price": 190.0, "last_price": 200.0,
            "market_value": 2400.0, "unrealized_pnl": 120.0, "unrealized_pnl_pct": 5.26,
        ])
        #expect(p.quantityLabel == "SHARES" && p.quantityText == "12.0000")
        #expect(!p.isOption && p.canClose)
        #expect(fmtMoney(p.marketValue) == "$2,400.00")
    }

    @Test func optionFillTotalsTimesOneHundred() {
        let t = Trade(json: ["symbol": "APH261002P00130000", "side": "sell", "qty": 1, "price": 1.25])
        #expect(t.isOption && t.quantityLabel == "CONTRACTS")
        #expect(fmtMoney(t.total) == "$125.00")
        let stock = Trade(json: ["symbol": "AAPL", "side": "buy", "qty": 12, "price": 200.0])
        #expect(fmtMoney(stock.total) == "$2,400.00")
    }

    @Test func rangeMoveAndDirection() {
        let pts = [HistPoint(ts: 1, value: 100), HistPoint(ts: 2, value: 101.5)]
        #expect(LivePositionMath.rangeText(pts, range: "1D", unrealizedPnl: -5) == "+1.50%  1D")
        #expect(!LivePositionMath.isUp([], unrealizedPnl: -1))
        #expect(LivePositionMath.isUp([], unrealizedPnl: nil))
        #expect(LivePositionMath.rangePct([HistPoint(ts: 1, value: 0), HistPoint(ts: 2, value: 5)]) == 0)
    }
}

@Suite(.serialized)
struct LiveTradingModelTests {
    private func liveStub(tradingActive: Bool = true) -> DataStub {
        let stub = DataStub()
        stub.handler = { req in
            switch req.path {
            case "/instances/i1/live-state":
                return (200, #"{"status": "active", "equity": 1000, "trading_active": \#(tradingActive), "positions": [{"symbol": "AAPL", "qty": 1}, {"symbol": "APH261002P00130000", "qty": -1, "asset_class": "us_option"}]}"#)
            case "/instances/i1/portfolio-history":
                return (200, #"{"timestamps": [1, 2], "values": [1, 2]}"#)
            case "/symbol-historicals":
                return (200, #"{"results": {"AAPL": [{"ts": 1, "value": 3}]}}"#)
            default:
                return (404, #"{"detail": "Not Found"}"#)
            }
        }
        return stub
    }

    @Test func adaptiveCadenceFollowsTradingActive() async {
        let active = liveStub(tradingActive: true)
        let model = LiveTradingModel(instanceId: "i1", repository: { LiveRepository(client: active.client) })
        await model.load()
        #expect(model.interval == .seconds(3))
        let idle = liveStub(tradingActive: false)
        let idleModel = LiveTradingModel(instanceId: "i1", repository: { LiveRepository(client: idle.client) })
        await idleModel.load()
        #expect(idleModel.interval == .seconds(10))
    }

    @Test func notRunningOn404KeepsThePreviousLiveState() async {
        let stub = liveStub()
        let model = LiveTradingModel(instanceId: "i1", repository: { LiveRepository(client: stub.client) })
        await model.load()
        stub.handler = { _ in (404, #"{"detail": "not running"}"#) }
        await model.pollCycle()
        #expect(model.value?.notRunning == true)
        #expect(model.value?.liveState != nil)
    }

    @Test func aFetchErrorKeepsDataAndRecordsTheMessage() async {
        let stub = liveStub()
        let model = LiveTradingModel(instanceId: "i1", repository: { LiveRepository(client: stub.client) })
        await model.load()
        stub.handler = { _ in (500, #"{"detail": "boom"}"#) }
        await model.pollCycle()
        #expect(model.value?.fetchError == "boom")
        #expect(model.value?.liveState?.equity == 1000)
    }

    @Test func setRangeRefetchesHistoryAndStockHistoricalsOnly() async {
        let stub = liveStub()
        let model = LiveTradingModel(instanceId: "i1", repository: { LiveRepository(client: stub.client) })
        await model.load()
        await model.setRange("1M")
        let history = stub.requests.first { $0.path == "/instances/i1/portfolio-history" }
        #expect(history?.queryItems == ["range": "1M"])
        let hist = stub.requests.first { $0.path == "/symbol-historicals" }
        #expect(hist?.queryItems == ["symbols": "AAPL", "range": "1M"])
        #expect(model.value?.equityHistory?.values == [1, 2])
        #expect(model.value?.positionHistoricals["AAPL"]?.count == 1)
    }

    @Test func aTerminalCommandDismissesAfterFiveSeconds() async {
        let stub = DataStub()
        stub.handler = { req in
            switch req.path {
            case "/instances/i1/live-command":
                return (200, #"{"command_id": "c1", "status": "completed", "result": {"ok": true}}"#)
            default:
                return (200, #"{"status": "active"}"#)
            }
        }
        let clock = ManualClock()
        let model = LiveTradingModel(instanceId: "i1", repository: { LiveRepository(client: stub.client) }, sleep: clock.sleep)
        await model.load()
        await model.runCommand("halt", ["reason": "risk breach"])
        #expect(stub.requests.first { $0.path == "/instances/i1/live-command" }?.jsonBody == ["type": "halt", "payload": ["reason": "risk breach"]])
        #expect(model.value?.commandToast?.status == "completed")
        #expect(model.value?.commandToast?.result?["ok"] == .bool(true))
        await clock.advance(by: .seconds(5))
        #expect(model.value?.commandToast == nil)
    }

    @Test func aPendingCommandIsPolledUntilTerminal() async {
        let stub = DataStub()
        stub.handler = { req in
            switch req.path {
            case "/instances/i1/live-command":
                return (200, #"{"command_id": "c1", "status": "pending"}"#)
            case "/live-commands/c1":
                return (200, #"{"command_id": "c1", "status": "running"}"#)
            default:
                return (200, #"{"status": "active"}"#)
            }
        }
        let clock = ManualClock()
        let model = LiveTradingModel(instanceId: "i1", repository: { LiveRepository(client: stub.client) }, sleep: clock.sleep)
        await model.load()
        await model.runCommand("close_position", ["symbol": "AAPL"])
        #expect(model.value?.commandToast?.isPending == true)
        await clock.advance(by: .seconds(1))
        #expect(await eventually { stub.requests.contains { $0.path == "/live-commands/c1" } })
        stub.handler = { req in
            if req.path == "/live-commands/c1" { return (200, #"{"command_id": "c1", "status": "failed", "error": "rejected"}"#) }
            return (200, #"{"status": "active"}"#)
        }
        await clock.advance(by: .seconds(1))
        #expect(await eventually { model.value?.commandToast?.status == "failed" })
        #expect(model.value?.commandToast?.error == "rejected")
        // Terminal: no further status polls.
        let polls = stub.requests.filter { $0.path == "/live-commands/c1" }.count
        await clock.advance(by: .seconds(3))
        #expect(stub.requests.filter { $0.path == "/live-commands/c1" }.count == polls)
    }

    @Test func aSendFailureShowsAFailedToastForSixSeconds() async {
        let stub = DataStub()
        stub.handler = { req in
            req.path == "/instances/i1/live-command" ? (503, #"{"detail": "broker down"}"#) : (200, #"{"status": "active"}"#)
        }
        let clock = ManualClock()
        let model = LiveTradingModel(instanceId: "i1", repository: { LiveRepository(client: stub.client) }, sleep: clock.sleep)
        await model.load()
        await model.runCommand("submit_order", buildOrderPayload(OrderForm(symbol: "AAPL", qty: "1")))
        #expect(model.value?.commandToast?.status == "failed")
        #expect(model.value?.commandToast?.error == "broker down")
        #expect(model.value?.commandToast?.commandId == nil)
        await clock.advance(by: .seconds(5))
        #expect(model.value?.commandToast != nil)
        await clock.advance(by: .seconds(1))
        #expect(model.value?.commandToast == nil)
    }

    @Test func dismissToastClearsIt() async {
        let stub = DataStub()
        stub.handler = { req in
            req.path == "/instances/i1/live-command" ? (200, #"{"command_id": "c9", "status": "completed"}"#) : (200, #"{"status": "active"}"#)
        }
        let model = LiveTradingModel(instanceId: "i1", repository: { LiveRepository(client: stub.client) }, sleep: { _ in try await Task.sleep(for: .seconds(3600)) })
        await model.load()
        await model.runCommand("halt", [:])
        model.dismissToast()
        #expect(model.value?.commandToast == nil)
    }
}
