import Foundation
import Testing
@testable import IntelliStock

// Wave 3 finding 9 and the Int-trap minor: typed or served numbers that are
// non-finite or overflow never crash.

@MainActor
@Suite struct W3CryptoNumberGuardTests {
    private func model() -> CryptoInstanceFormModel {
        CryptoInstanceFormModel(editInstanceId: nil, editConfig: nil, editStocks: nil, repository: { CryptoRepository(client: DataStub().client) })
    }

    @Test func pastingNaNOrInfinityIntoAPercentIsRejected() {
        let m = model()
        let id = m.rows[0].id
        m.onPctChanged(id, "NaN")
        #expect(m.rows[0].pct == 0)
        #expect(m.rows[0].pctText == "NaN")
        #expect(m.rows[0].usdText == "0")
        m.onPctChanged(id, "Infinity")
        #expect(m.rows[0].pct == 0)
        m.onUsdChanged(id, "NaN")
        #expect(m.rows[0].pct == 0)
        #expect(m.rows[0].pctText == "0")
        m.onUsdChanged(id, "1e400")
        #expect(m.rows[0].pct == 0)
    }

    @Test func formattersSurviveNonFiniteAndHugeValues() {
        #expect(CryptoInstanceFormModel.fmtUsd(.nan) == "$0")
        #expect(CryptoInstanceFormModel.fmtUsd(.infinity) == "$0")
        #expect(CryptoInstanceFormModel.fmtUsd(1e30) == "$9223372036854775807")
        #expect(CryptoInstanceFormModel.fmtUsd(12.5) == "$13")
        #expect(CryptoInstanceFormModel.fmtNum(.infinity) == "Infinity")
        #expect(CryptoInstanceFormModel.fmtNum(.nan) == "NaN")
        #expect(CryptoInstanceFormModel.fmtNum(1e20) == "9223372036854775807")
        #expect(CryptoInstanceFormModel.fmtNum(42) == "42")
        #expect(CryptoInstanceFormModel.fmtNum(4.24) == "4.2")
    }
}

@MainActor
@Suite struct W3KalshiNumberGuardTests {
    private func model(edit: JSONObject? = nil) -> KalshiInstanceFormModel {
        KalshiInstanceFormModel(
            initialBrokerageId: "",
            editInstanceId: edit == nil ? nil : "k1",
            editName: nil,
            editConfig: edit,
            repository: { KalshiRepository(client: DataStub().client) }
        )
    }

    @Test func aNaNBankrollThenAPresetDoesNotCrash() {
        let m = model()
        m.manualBankroll = "NaN"
        #expect(m.effectiveBankroll == 0)
        m.applyPreset("high")
        #expect(m.dailyLoss == "1")
    }

    @Test func aTwentyOneDigitBankrollClampsTheDailyLoss() {
        let m = model()
        m.manualBankroll = "100000000000000000000"
        // 1e20 × 15 % is past Int.max: this trapped in Int(_:).
        m.applyPreset("max")
        #expect(m.dailyLoss == String(1 << 30))
    }

    @Test func anExtremeServedIntegerPrefillsWithoutTrapping() {
        let m = model(edit: ["edge_threshold": .int(Int.max), "max_open_exposure_frac": .double(1e300)])
        #expect(!m.edge.isEmpty)
        #expect(!m.exposure.isEmpty)
    }

    @Test func kalshiNumNeverTraps() {
        #expect(KalshiFormat.num(1e20) == "9223372036854775807")
        #expect(KalshiFormat.num(-1e20) == "-9223372036854775808")
        #expect(KalshiFormat.num(3.0) == "3")
        #expect(KalshiFormat.num(Double.nan) == "NaN")
    }
}

@MainActor
@Suite struct W3AgentScheduleGuardTests {
    @Test func aFifteenDigitDelayDoesNotOverflow() async {
        let stub = DataStub()
        let routes = ChatStubRoutes()
        routes.stub = stub
        routes.set("GET /agent/runs", #"{"runs":[],"total":0,"total_pages":1,"page":1}"#)
        routes.set("GET /agent/control", #"{"running":true,"paused":true}"#)
        stub.handler = { routes.answer($0) }
        let client = stub.client
        let clock = ManualClock()
        let now = Date(timeIntervalSince1970: 1_700_000_000)
        let model = AgentRunsModel(repository: { AgentRepository(client: client) }, now: { now }, sleep: clock.sleep)
        await model.refreshNow()

        await model.scheduleResume(999_999_999_999_999)
        let s = model.value
        #expect((s?.scheduledTotalMs ?? 0) > 0)
        #expect(s?.scheduledTotalMs == AgentRunsModel.maxScheduleMs)
        #expect((s?.countdownFraction(now: now) ?? 1) < 0.001)
        #expect((s?.countdownSecsRemaining(now: now) ?? 0) > 0)

        await model.scheduleResume(Int.max)
        #expect(model.value?.scheduledTotalMs == AgentRunsModel.maxScheduleMs)
        model.cancelCountdown()
    }

    @Test func countdownMathSurvivesAnyResumeDate() {
        var s = AgentRunsState()
        let now = Date(timeIntervalSince1970: 1_700_000_000)
        s.scheduledResumeAt = now.addingTimeInterval(1e300)
        s.scheduledTotalMs = Int.max
        _ = s.countdownFraction(now: now)
        _ = s.countdownSecsRemaining(now: now)
        s.scheduledResumeAt = Date.distantFuture.addingTimeInterval(1e300)
        _ = s.countdownFraction(now: now)
        _ = s.countdownSecsRemaining(now: now)
    }
}
