import Foundation
import Testing
@testable import IntelliStock

// Wave 3 finding 8 and the double-submit minors: one tap, one request.

/// A stub whose answers are held back long enough for a second tap to land
/// while the first request is in flight.
private func slowStub(_ json: String) -> DataStub {
    let stub = DataStub()
    stub.handler = { _ in
        Thread.sleep(forTimeInterval: 0.2)
        return (200, json)
    }
    return stub
}

private func posts(_ stub: DataStub, _ path: String) -> Int {
    stub.requests.filter { $0.method == "POST" && $0.path == path }.count
}

@MainActor
@Suite struct W3DoubleSubmitTests {
    @Test func linkingABrokerageCannotSubmitAgainAfterSuccess() async {
        let stub = DataStub(json: #"{"id": "b1"}"#)
        let client = stub.client
        let form = LinkBrokerageFormModel(editAccount: nil, repository: { BrokerageRepository(client: client) })
        form.alpacaName = "Paper"
        form.alpacaKey = "PK"
        form.alpacaSecret = "SK"
        #expect(await form.submitAlpaca(bypassTest: true))
        #expect(form.finished)
        #expect(form.locked)
        #expect(!form.submitting)

        // The success line holds for 1.2 s before the sheet closes: a second
        // tap (either button) sends nothing.
        #expect(await form.submitAlpaca(bypassTest: true) == false)
        #expect(await form.submitAlpaca() == false)
        #expect(await form.submitBinanceus() == false)
        #expect(posts(stub, "/brokerages") == 1)
        #expect(form.submitMsg == "Account linked!")
    }

    @Test func aKalshiInstanceSubmitInFlightIgnoresASecondTap() async {
        let stub = slowStub("{}")
        let client = stub.client
        let m = KalshiInstanceFormModel(initialBrokerageId: "b1", repository: { KalshiRepository(client: client) })
        m.name = "N"
        async let first = m.submit()
        async let second = m.submit()
        let results = await [first, second]
        #expect(results.compactMap { $0 } == ["b1"])
        #expect(posts(stub, "/brokerages/b1/kalshi/instances") == 1)
    }

    @Test func aKalshiBacktestSubmitInFlightIgnoresASecondTap() async {
        let stub = slowStub(#"{"id": "bt1", "backtests": []}"#)
        let client = stub.client
        let m = KalshiBacktestModel(instanceId: "i1", repository: { KalshiRepository(client: client) })
        let cal = Calendar.current
        m.start = cal.date(from: DateComponents(year: 2026, month: 3, day: 1))
        m.end = cal.date(from: DateComponents(year: 2026, month: 3, day: 9))
        async let first = m.submit()
        async let second = m.submit()
        let results = await [first, second]
        #expect(results.compactMap { $0 }.count == 1)
        #expect(stub.requests.filter { $0.method == "POST" }.count == 1)
    }

    @Test func aCryptoInstanceSubmitInFlightIgnoresASecondTap() async {
        let stub = slowStub(#"{"id": "c1"}"#)
        let client = stub.client
        let m = CryptoInstanceFormModel(repository: { CryptoRepository(client: client) })
        m.instanceIdText = "c1"
        async let first = m.submit()
        async let second = m.submit()
        let results = await [first, second]
        #expect(results.filter { $0 }.count == 1)
        #expect(posts(stub, "/instances") == 1)
    }

    @Test func aCryptoBacktestSubmitInFlightIgnoresASecondTap() async {
        let inst = Instance(json: ["id": "c1", "stocks": ["BTC/USD"]])
        let stub = slowStub(#"{"backtest_id": 5}"#)
        let client = stub.client
        let m = CryptoBacktestFormModel(inst: inst, repository: { CryptoRepository(client: client) })
        async let first = m.submit()
        async let second = m.submit()
        let results = await [first, second]
        #expect(results.compactMap { $0 } == ["5"])
        #expect(stub.requests.filter { $0.method == "POST" }.count == 1)
    }
}

/// Connect's keyboard Go is guarded like the button.
@MainActor
@Suite struct W3ConnectGoTests {
    @Test func goWhileProbingDoesNothing() async {
        let services = AppServices(
            storage: InMemorySecureStorage(),
            biometrics: FakeBiometrics(available: false, authResult: false),
            widgetSync: WidgetSyncProbe().sync,
            urlSession: DataStubProtocol.session,
            pushRegistrar: FakePushRegistrar(grant: false)
        )
        let gate = SwingTestGate()
        var probes = 0
        let model = ConnectModel(services: services, probe: { _ in
            probes += 1
            await gate.wait()
            return true
        })
        model.url = "https://new.example.test"
        let first = Task { await model.submit() }
        #expect(await eventually { model.probing })
        #expect(await model.submit() == nil)
        gate.open()
        #expect(await first.value == .configured)
        #expect(probes == 1)
    }
}
