import Foundation
import Testing
@testable import IntelliStock

/// Crypto view-model logic (no Dart tests existed): allocation maths, the
/// create / PATCH bodies, the backtest sheet and the detail screen helpers.
@MainActor
struct CryptoInstanceFormModelTests {
    private func model(_ stub: DataStub = DataStub(), editId: String? = nil, config: JSONObject? = nil, stocks: [String]? = nil) -> CryptoInstanceFormModel {
        CryptoInstanceFormModel(
            editInstanceId: editId, editName: editId == nil ? nil : "Bot", editBrokerageId: editId == nil ? nil : "b1",
            editConfig: config, editStocks: stocks,
            repository: { CryptoRepository(client: stub.client) }
        )
    }

    @Test func createStartsWithTheMockupAllocation() {
        let m = model()
        #expect(m.rows.map(\.sym) == ["BTC", "ETH"])
        #expect(m.fixedSum == 30 && m.dynPct == 70 && !m.over)
        #expect(m.rows.map(\.pctText) == ["10", "20"])
        #expect(m.slices.map(\.sector) == ["BTC", "ETH", "Dynamic"])
    }

    @Test func prefillReadsAllocationsBandAndStrategy() {
        let m = model(editId: "c1", config: [
            "band": "LOW", "strategy": "MEANREV",
            "allocations": [["symbol": "btc/usd", "pct": 0.25], ["symbol": "SOL/USD", "pct": 0.125]],
        ])
        #expect(m.band == "low")
        #expect(m.strategy == "Meanrev")
        #expect(m.rows.map(\.sym) == ["BTC", "SOL"])
        #expect(m.rows.map(\.pctText) == ["25", "12.5"])
        #expect(!m.weightsUnknown)
    }

    @Test func prefillFallsBackToAnEvenSplitOfTheStocks() {
        let m = model(editId: "c1", config: nil, stocks: ["BTC/USD", "ETH/USD", "SOL/USD"])
        #expect(m.weightsUnknown)
        #expect(m.rows.map(\.pct) == [33.33, 33.33, 33.33])
        #expect(m.rows.map(\.pctText) == ["33.3", "33.3", "33.3"])
    }

    @Test func percentAndDollarEditsConvertAndClamp() async {
        let stub = DataStub(json: #"{"cash": 1000, "positions": [{"marketValue": 1000}]}"#)
        let m = model(stub)
        await m.selectBrokerage("b1")
        #expect(m.equity == 2000)
        #expect(m.rows.map(\.usdText) == ["200", "400"])
        let btc = m.rows[0].id
        m.onPctChanged(btc, "150")
        #expect(m.rows[0].pct == 100 && m.rows[0].usdText == "2000")
        #expect(m.over && m.dynPct == 0)
        m.onUsdChanged(btc, "500")
        #expect(m.rows[0].pct == 25 && m.rows[0].pctText == "25")
        m.removeCoin(btc)
        #expect(m.rows.map(\.sym) == ["ETH"])
        m.addCoin("SOL")
        #expect(m.rows.last?.pctText == "0" && m.rows.last?.usdText == "0")
        #expect(!m.remainingCoins.contains { $0.sym == "SOL" })
    }

    @Test func strategyPickAppliesTheRecommendedBand() {
        let m = model()
        m.selectStrategy("Fast")
        #expect(m.band == "high")
        m.selectStrategy("Momentum")
        #expect(m.band == "medium")
        #expect(CryptoCatalog.recommendedBand(for: "Meanrev") == "low")
        #expect(CryptoCatalog.strategyBlurb("Fast").hasPrefix("Tactical Donchian"))
    }

    @Test func createBodyMatchesTheDartMap() throws {
        let stub = DataStub()
        let m = model(stub)
        m.instanceIdText = " crypto-main "
        m.band = "low"
        let body = m.createBody()
        #expect(body.entries.map(\.key) == ["id", "granularity", "run_command", "kind", "stocks", "crypto_config"])
        #expect(body["id"] == "crypto-main")
        #expect(body["granularity"] == "3600")
        #expect(body["run_command"] == false && body["kind"] == "crypto")
        #expect(body["stocks"] == ["BTC/USD", "ETH/USD"])
        let cfg = try #require(body["crypto_config"]?.orderedObject)
        #expect(cfg.entries.map(\.key) == ["band", "strategy", "allocations"])
        #expect(cfg["strategy"] == "momentum")
        #expect(cfg["allocations"] == [["symbol": "BTC/USD", "pct": 0.1], ["symbol": "ETH/USD", "pct": 0.2]])
    }

    @Test func editBodySendsEveryEditableField() {
        let m = model(editId: "c1", config: ["band": "high", "allocations": [["symbol": "BTC/USD", "pct": 0.5]]])
        m.name = " New "
        let body = m.editBody()
        #expect(body.entries.map(\.key) == ["name", "brokerage_id", "granularity", "crypto_config", "stocks"])
        #expect(body["name"] == "New" && body["brokerage_id"] == "b1" && body["granularity"] == "300")
    }

    @Test func resolveStrategyIdMatchesByName() async {
        let stub = DataStub()
        stub.handler = { req in
            if req.url?.path == "/strategies" { return (200, #"{"strategies": [{"id": 7, "name": "momentum"}, {"id": "x", "name": "fast"}]}"#) }
            return (200, #"{"accounts": [{"id": "a1", "brokerage_type": "robinhood"}, {"id": "a2", "brokerage_type": "alpaca"}]}"#)
        }
        let m = model(stub)
        await m.loadSelectors()
        #expect(m.brokerageId == "a2")
        #expect(m.resolveStrategyId() == 7)
        #expect(m.createBody()["strategy_id"] == 7)
        m.selectStrategy("Fast")
        #expect(m.resolveStrategyId() == nil)
    }

    @Test func submitValidatesIdAndOverAllocation() async {
        let stub = DataStub()
        let m = model(stub)
        #expect(await m.submit() == false)
        #expect(m.err == "Instance ID is required")
        m.instanceIdText = "c"
        m.onPctChanged(m.rows[0].id, "95")
        #expect(await m.submit() == false)
        #expect(m.err == "Over-allocated — fixed weights exceed 100%")
        m.onPctChanged(m.rows[0].id, "10")
        #expect(await m.submit())
        #expect(stub.last?.method == "POST" && stub.last?.path == "/instances")
    }

    @Test func formatHelpers() {
        #expect(CryptoInstanceFormModel.fmtNum(12) == "12")
        #expect(CryptoInstanceFormModel.fmtNum(12.25) == "12.3")
        #expect(CryptoInstanceFormModel.fmtUsd(199.5) == "$200")
        #expect(CryptoCatalog.baseOf(" eth/usd ") == "ETH")
        #expect(CryptoCatalog.baseOf("/X") == "/X")
    }
}

@MainActor
struct CryptoBacktestFormModelTests {
    @Test func defaultsCadenceAndBody() async throws {
        var inst = Instance(json: ["id": "c1", "name": "Bot", "stocks": ["BTC/USD"]])
        inst.cryptoConfig = ["band": "high"]
        let stub = DataStub(json: #"{"backtest_id": 55}"#)
        let now = Date(timeIntervalSince1970: 1_780_000_000)
        let m = CryptoBacktestFormModel(inst: inst, now: now, repository: { CryptoRepository(client: stub.client) })
        #expect(m.gran == "300")
        #expect(m.cadenceLabel == "High · ~5-minute bars")
        #expect(m.end.timeIntervalSince(m.start) == 90 * 86400)
        #expect(m.tickers == ["BTC"])
        m.cash = "2500"
        m.feeVenue = "kraken"
        #expect(await m.submit() == "55")
        let body = try #require(stub.last?.jsonBody)
        #expect(body["initial_cash"] == 2500.0)
        #expect(body["emulate_fee_venue"] == "kraken")
        #expect(body["granularity"] == "300")
        // A submitted sheet stays busy (it closes); validate on a fresh one.
        let fresh = CryptoBacktestFormModel(inst: inst, now: now, repository: { CryptoRepository(client: stub.client) })
        fresh.start = fresh.end
        #expect(await fresh.submit() == nil)
        #expect(fresh.err == "End date must be after start date")
    }

    @Test func missingBandFallsBackToMedium() {
        let inst = Instance(json: ["id": "c1"])
        let m = CryptoBacktestFormModel(inst: inst, repository: { CryptoRepository(client: DataStub().client) })
        #expect(m.gran == "900")
        #expect(m.cadenceLabel == "Medium · ~15-minute bars")
        #expect(m.feeCaption == "Uses this instance's own brokerage fee.")
    }
}

@MainActor
struct CryptoInstanceDetailModelTests {
    @Test func loadBackfillsTheCryptoConfigFromTheList() async {
        let stub = DataStub()
        stub.handler = { req in
            switch req.url?.path {
            case "/instances/c1": return (200, #"{"instance": {"id": "c1", "name": "Bot", "brokerage_id": "b1", "run_command": true}}"#)
            case "/instances": return (200, #"{"instances": [{"id": "c1", "kind": "crypto", "crypto_config": {"band": "low", "strategy": "meanrev", "allocations": [{"symbol": "BTC/USD", "pct": 0.4}]}, "stocks": ["BTC/USD"]}]}"#)
            case "/brokerages": return (200, #"{"accounts": [{"id": "b1", "account_name": "Main", "alpaca_paper": true}]}"#)
            case "/instances/c1/backtests": return (200, #"{"backtests": [{"id": 9, "status": "running"}]}"#)
            default: return (200, #"{"cash": 100, "positions": []}"#)
            }
        }
        let m = CryptoInstanceDetailModel(instanceId: "c1", repository: { CryptoRepository(client: stub.client) })
        await m.load()
        #expect(m.band == "low")
        #expect(m.inst?.stocks == ["BTC/USD"])
        #expect(m.isPaper)
        #expect(m.value == 100)
        #expect(m.anyRunning)
        #expect(m.slices.map(\.sector) == ["BTC", "Dynamic"])
        #expect(m.allocChips.map(\.text) == ["BTC 40%", "Dynamic 60%"])
    }

    @Test func formatHelpers() {
        #expect(CryptoInstanceDetailModel.fmtUsd(1234567.4) == "$1,234,567")
        #expect(CryptoInstanceDetailModel.fmtUsd(-1234) == "$-1,234")
        #expect(CryptoInstanceDetailModel.fmtUsd(nil) == "$0")
        #expect(CryptoInstanceDetailModel.fmtPnl(12) == "+$12")
        #expect(CryptoInstanceDetailModel.fmtPct(-1.234) == "-1.23%")
        #expect(CryptoInstanceDetailModel.fmtDuration(3725) == "1h 2m 5s")
    }
}

@MainActor
struct CryptoModelTests {
    @Test func actionsSwallowErrorsAndRefetch() async {
        let stub = DataStub()
        stub.handler = { req in
            if req.httpMethod == "POST" { return (500, #"{"detail": "nope"}"#) }
            return (200, #"{"instances": [{"id": "c1", "kind": "crypto"}, {"id": "e1"}]}"#)
        }
        let m = CryptoModel(repository: { CryptoRepository(client: stub.client) })
        await m.start("c1")
        #expect(!m.isBusy("c1"))
        #expect(m.instances.value?.map(\.id) == ["c1"])
        await m.delete("c1")
        #expect(stub.requests.contains { $0.httpMethod == "DELETE" && $0.url?.query == "force=true" })
    }
}
