import Foundation
import Testing
@testable import IntelliStock

/// The AI analysis screen's model (filtering, selection, explain, polling,
/// account), its settings model, the repository's wire format, and the pure
/// helpers behind the screen.
@MainActor
struct AnalysisModelTests {
    /// Six events: two earnings, one analyst (explained), then one each of
    /// unexplained, pending and failed. Dates sit on the fixture's bars.
    nonisolated static let sixEvents = [
        AnalysisFixtures.event("2026-10-09", status: "explained", category: "earnings", label: "Earnings"),
        AnalysisFixtures.event("2026-10-08", status: "unexplained"),
        AnalysisFixtures.event("2026-10-06", status: "explained", category: "analyst", label: "Analysts"),
        AnalysisFixtures.event("2026-10-05", status: "pending"),
        AnalysisFixtures.event("2026-10-04", status: "failed"),
        AnalysisFixtures.event("2026-10-03", status: "explained", category: "earnings", label: "Earnings"),
    ]

    private func model(_ stub: DataStub, symbol: String? = "NVDA", clock: ManualClock? = nil, holdings: @escaping (String) async throws -> [String] = { _ in [] }) -> AnalysisModel {
        AnalysisModel(
            symbol: symbol,
            repository: { AnalysisRepository(client: stub.client) },
            holdings: holdings,
            sleep: clock?.sleep ?? realPollingSleep
        )
    }

    /// GET /analysis/moves requests seen so far.
    nonisolated private static func movesRequests(_ stub: DataStub?) -> Int {
        stub?.requests.filter { $0.method == "GET" && $0.path == "/analysis/moves" }.count ?? 0
    }

    // MARK: Filtering and counts

    @Test func chipsCountEachCategoryAndTheUnexplained() async throws {
        let stub = DataStub(json: AnalysisFixtures.moves(events: Self.sixEvents))
        let m = model(stub)
        await m.load()
        let chips = m.chips
        #expect(chips.map(\.label) == ["All", "Earnings", "Analysts", "Not explained"])
        #expect(chips.map(\.count) == [6, 2, 1, 3])
        #expect(chips.map(\.filter) == [.all, .category("earnings"), .category("analyst"), .notExplained])
        #expect(chips[1].family == .company && chips[2].family == .street)
        #expect(chips[0].family == nil && chips[3].family == nil)
        // Macro is a known category with no events: no chip.
        #expect(!chips.contains { $0.filter == .category("macro") })
    }

    @Test func filterNarrowsTheListAndTheMarkers() async throws {
        let stub = DataStub(json: AnalysisFixtures.moves(events: Self.sixEvents))
        let m = model(stub)
        await m.load()
        #expect(m.visibleEvents.count == 6)
        #expect(m.markers.count == 6)
        // Markers run oldest first, each on its bar.
        #expect(m.markers.map(\.index) == [2, 3, 4, 5, 7, 8])

        m.setFilter(.category("earnings"))
        #expect(m.visibleEvents.map(\.date) == ["2026-10-09", "2026-10-03"])
        #expect(m.markers.map(\.index) == [2, 8])

        m.setFilter(.notExplained)
        #expect(m.visibleEvents.map(\.status) == [.unexplained, .pending, .failed])
    }

    @Test func aFilterWithNothingLeftFallsBackToAll() async throws {
        let stub = DataStub(json: AnalysisFixtures.moves(events: Self.sixEvents))
        let m = model(stub)
        await m.load()
        m.setFilter(.category("analyst"))
        // The next answer has no analyst event any more.
        stub.respond(json: AnalysisFixtures.moves(events: [Self.sixEvents[0]]))
        await m.load()
        #expect(m.filter == .all)
        #expect(m.visibleEvents.count == 1)
    }

    @Test func chipsKeepServerOrderAndAppendUnknownCategories() throws {
        let a = MoveAnalysis(json: try AnalysisFixtures.json(AnalysisFixtures.moves(events: [
            AnalysisFixtures.event("2026-10-02", status: "explained", category: "lockup", label: "Lock-up"),
            AnalysisFixtures.event("2026-10-03", status: "explained", category: "analyst", label: "Analysts"),
            AnalysisFixtures.event("2026-10-04", status: "explained", category: "earnings", label: "Earnings"),
        ])))
        #expect(AnalysisFormat.chips(a).map(\.label) == ["All", "Earnings", "Analysts", "Lock-up"])
        #expect(AnalysisFormat.chips(a).last?.family == .other)
    }

    // MARK: Selection

    @Test func tappingADayWithAnEventOpensIt() async throws {
        let stub = DataStub(json: AnalysisFixtures.moves(events: Self.sixEvents))
        let m = model(stub)
        await m.load()
        // Bar 5 is 2026-10-06, the analyst event.
        m.tapDay(5)
        #expect(m.selectedDay == 5)
        #expect(m.selectedBar?.date == "2026-10-06")
        #expect(m.detail == AnalysisDetailRequest(id: "NVDA|2026-10-06"))
        #expect(m.event(id: "NVDA|2026-10-06")?.categoryLabel == "Analysts")

        // The same day again returns the hero to the range.
        m.detail = nil
        m.tapDay(5)
        #expect(m.selectedDay == nil)
        #expect(m.detail == nil)

        // A day with no event only selects it.
        m.tapDay(0)
        #expect(m.selectedDay == 0)
        #expect(m.detail == nil)

        // Out of range is ignored.
        m.tapDay(99)
        #expect(m.selectedDay == 0)
    }

    @Test func aFilteredOutEventDoesNotOpen() async throws {
        let stub = DataStub(json: AnalysisFixtures.moves(events: Self.sixEvents))
        let m = model(stub)
        await m.load()
        m.setFilter(.category("earnings"))
        m.tapDay(5) // the analyst day
        #expect(m.selectedDay == 5)
        #expect(m.detail == nil)
    }

    @Test func changingSymbolOrRangeClearsSelectionAndRefetches() async throws {
        let stub = DataStub(json: AnalysisFixtures.moves(events: Self.sixEvents))
        let m = model(stub)
        await m.load()
        m.tapDay(5)
        #expect(m.detail != nil)

        let before = m.loadKey
        m.select(symbol: " aapl ")
        #expect(m.symbol == "AAPL")
        #expect(m.loadKey != before)
        #expect(m.selectedDay == nil && m.detail == nil)
        #expect(m.moves.isLoading)
        // The same symbol again changes nothing.
        let key = m.loadKey
        m.select(symbol: "AAPL")
        #expect(m.loadKey == key)

        await m.load()
        #expect(stub.last?.queryItems == ["symbol": "AAPL", "range": "3M"])
        m.setRange(.oneYear)
        #expect(m.moves.isLoading)
        await m.load()
        #expect(stub.last?.queryItems == ["symbol": "AAPL", "range": "1Y"])
    }

    @Test func aLateAnswerForTheOldSymbolIsDropped() async throws {
        let stub = DataStub()
        // NVDA's answer is held until the screen has moved on to AAPL.
        let gate = DispatchSemaphore(value: 0)
        stub.handler = { req in
            let symbol = req.queryItems["symbol"] ?? ""
            if symbol == "NVDA" { gate.wait() }
            return (200, AnalysisFixtures.moves(symbol: symbol, events: []))
        }
        let m = model(stub)
        let first = Task { await m.load() }
        #expect(await eventually { stub.requests.count == 1 })
        m.select(symbol: "AAPL")
        gate.signal()
        await first.value
        // NVDA's answer arrived after the switch: still loading AAPL.
        #expect(m.moves.isLoading)
        await m.load()
        #expect(m.analysis?.symbol == "AAPL")
    }

    // MARK: Errors

    @Test func unknownSymbolIsNotFound() async throws {
        let stub = DataStub(status: 404, json: #"{"detail": "Unknown symbol ZZZZ"}"#)
        let m = model(stub, symbol: "ZZZZ")
        await m.load()
        #expect(m.isNotFound)
        #expect(m.moves.errorMessage == "Unknown symbol ZZZZ")
    }

    @Test func aFailedRefreshKeepsTheData() async throws {
        let stub = DataStub(json: AnalysisFixtures.moves(events: Self.sixEvents))
        let m = model(stub)
        await m.load()
        stub.respond(status: 500, json: #"{"detail": "boom"}"#)
        await m.load()
        #expect(m.analysis?.events.count == 6)
        #expect(!m.isNotFound)
    }

    @Test func otherFailuresShowTheMessageAndRetryRecovers() async throws {
        let stub = DataStub(status: 500, json: #"{"detail": "yfinance timed out"}"#)
        let m = model(stub)
        await m.load()
        #expect(!m.isNotFound)
        #expect(m.moves.errorMessage == "yfinance timed out")
        stub.respond(json: AnalysisFixtures.moves(events: []))
        await m.retry()
        #expect(m.analysis?.events.isEmpty == true)
    }

    @Test func noSymbolMakesNoRequest() async {
        let stub = DataStub()
        let m = model(stub, symbol: "  ")
        #expect(m.symbol == nil)
        await m.load()
        #expect(stub.requests.isEmpty)
    }

    // MARK: Explain and polling

    @Test func explainMovesQueuesEverythingThenStartsPolling() async throws {
        let stub = DataStub(json: AnalysisFixtures.moves(events: Self.sixEvents))
        let m = model(stub)
        await m.load()
        #expect(m.canExplainAll)
        #expect(!m.isPolling)
        let idle = m.pollKey

        stub.handler = { req in
            if req.method == "POST" { return (200, #"{"queued": 3}"#) }
            return (200, AnalysisFixtures.moves(pending: 3, events: AnalysisModelTests.sixEvents))
        }
        await m.explainAll()
        let post = try #require(stub.requests.first { $0.method == "POST" })
        #expect(post.path == "/analysis/moves/explain")
        // `dates` left out: every unexplained or failed event.
        #expect(post.jsonBody == ["symbol": "NVDA", "range": "3M", "force": false])
        #expect(m.isPolling)
        #expect(m.pollKey != idle)
        #expect(!m.explaining)
        #expect(m.actionError == nil)
    }

    @Test func explainOneEventAndReRunForcesIt() async throws {
        let stub = DataStub()
        stub.handler = { req in
            req.method == "POST" ? (200, #"{"queued": 1}"#) : (200, AnalysisFixtures.moves(events: AnalysisModelTests.sixEvents))
        }
        let m = model(stub)
        await m.load()
        let unexplained = try #require(m.event(id: "NVDA|2026-10-08"))
        await m.explain(unexplained)
        let first = try #require(stub.requests.last { $0.method == "POST" })
        #expect(first.jsonBody == ["symbol": "NVDA", "range": "3M", "dates": ["2026-10-08"], "force": false])

        let explained = try #require(m.event(id: "NVDA|2026-10-09"))
        await m.explain(explained)
        let rerun = try #require(stub.requests.last { $0.method == "POST" })
        #expect(rerun.jsonBody == ["symbol": "NVDA", "range": "3M", "dates": ["2026-10-09"], "force": true])
        #expect(m.explainingIds.isEmpty)
    }

    @Test func explainWithoutAModelShowsTheServersMessage() async throws {
        let stub = DataStub()
        stub.handler = { req in
            req.method == "POST"
                ? (400, #"{"detail": "Choose a model in Settings first"}"#)
                : (200, AnalysisFixtures.moves(configured: false, events: AnalysisModelTests.sixEvents))
        }
        let m = model(stub)
        await m.load()
        #expect(!m.modelConfigured)
        #expect(!m.canExplainAll)
        #expect(!m.canExplain(try #require(m.visibleEvents.first)))
        await m.explainAll()
        #expect(m.actionError == "Choose a model in Settings first")
    }

    @Test func nothingQueuedSaysSo() async throws {
        let stub = DataStub()
        stub.handler = { req in
            req.method == "POST" ? (200, #"{"queued": 0}"#) : (200, AnalysisFixtures.moves(events: AnalysisModelTests.sixEvents))
        }
        let m = model(stub)
        await m.load()
        await m.explainAll()
        #expect(m.toast?.message == "Nothing to explain")
    }

    @Test func pollingStopsWhenPendingReachesZero() async throws {
        let stub = DataStub()
        // pending 2, then 1, then 0.
        stub.handler = { [weak stub] _ in
            let n = AnalysisModelTests.movesRequests(stub)
            return (200, AnalysisFixtures.moves(pending: max(3 - n, 0), events: AnalysisModelTests.sixEvents))
        }
        let clock = ManualClock()
        let m = model(stub, clock: clock)
        await m.load()
        #expect(m.analysis?.pending == 2)
        #expect(m.isPolling)

        let poll = Task { await m.pollWhilePending() }
        #expect(await eventually { clock.pendingCount == 1 })
        await clock.advance(by: AnalysisModel.pollInterval)
        #expect(await eventually { m.analysis?.pending == 1 })
        #expect(await eventually { clock.pendingCount == 1 })
        await clock.advance(by: AnalysisModel.pollInterval)
        #expect(await eventually { m.analysis?.pending == 0 })

        await poll.value
        #expect(!m.isPolling)
        #expect(Self.movesRequests(stub) == 3)
        #expect(clock.requested == [.seconds(3), .seconds(3)])
        #expect(clock.pendingCount == 0)
    }

    @Test func pollingStopsWhenTheSymbolChanges() async throws {
        let stub = DataStub(json: AnalysisFixtures.moves(pending: 2, events: Self.sixEvents))
        let clock = ManualClock()
        let m = model(stub, clock: clock)
        await m.load()
        let poll = Task { await m.pollWhilePending() }
        #expect(await eventually { clock.pendingCount == 1 })
        m.select(symbol: "AAPL")
        await clock.advance(by: AnalysisModel.pollInterval)
        await poll.value
        // Woke up, saw a new symbol, fetched nothing.
        #expect(Self.movesRequests(stub) == 1)
    }

    @Test func pollingStopsWhenTheViewGoesAway() async throws {
        let stub = DataStub(json: AnalysisFixtures.moves(pending: 2, events: Self.sixEvents))
        let clock = ManualClock()
        let m = model(stub, clock: clock)
        await m.load()
        let poll = Task { await m.pollWhilePending() }
        #expect(await eventually { clock.pendingCount == 1 })
        poll.cancel()
        await poll.value
        #expect(clock.pendingCount == 0)
        #expect(Self.movesRequests(stub) == 1)
    }

    @Test func nothingPendingMeansNoPolling() async throws {
        let stub = DataStub(json: AnalysisFixtures.moves(pending: 0, events: Self.sixEvents))
        let clock = ManualClock()
        let m = model(stub, clock: clock)
        await m.load()
        await m.pollWhilePending()
        #expect(clock.requested.isEmpty)
    }

    // MARK: Account

    @Test func holdingsBecomeQuickPicksAndPickTheFirstSymbol() async throws {
        let stub = DataStub(json: AnalysisFixtures.portfolio)
        let m = model(stub, symbol: nil, holdings: { id in
            #expect(id == "acct-1")
            return ["tqqq", "QCOM261009P00177500", "GLD", "TQQQ", "BTC/USD"]
        })
        await m.loadAccount("acct-1", isStockAccount: true)
        #expect(m.quickPicks == ["TQQQ", "GLD"])
        #expect(m.symbol == "TQQQ")
        let request = try #require(stub.requests.first { $0.path == "/analysis/portfolio" })
        #expect(request.queryItems == ["brokerage_id": "acct-1"])
        #expect(m.portfolio?.value?.holdings.first?.symbol == "TQQQ")
    }

    @Test func aChosenSymbolIsKeptWhenHoldingsArrive() async {
        let stub = DataStub(json: AnalysisFixtures.portfolio)
        let m = model(stub, symbol: "NVDA", holdings: { _ in ["TQQQ"] })
        await m.loadAccount("acct-1", isStockAccount: true)
        #expect(m.symbol == "NVDA")
        #expect(m.quickPicks == ["TQQQ"])
    }

    @Test func aKalshiAccountAsksForNothing() async {
        let stub = DataStub(json: AnalysisFixtures.portfolio)
        let m = model(stub, holdings: { _ in ["X"] })
        await m.loadAccount("kalshi", isStockAccount: false)
        #expect(m.portfolio == nil)
        #expect(m.quickPicks.isEmpty)
        #expect(stub.requests.isEmpty)
        await m.loadAccount(nil, isStockAccount: false)
        #expect(m.accountId == nil)
    }

    @Test func aPortfolioFailureShowsAndRetries() async {
        let stub = DataStub(status: 500, json: #"{"detail": "positions unavailable"}"#)
        let m = model(stub)
        await m.loadAccount("acct-1", isStockAccount: true)
        #expect(m.portfolio?.errorMessage == "positions unavailable")
        stub.respond(json: AnalysisFixtures.portfolio)
        await m.reloadPortfolio()
        #expect(m.portfolio?.value?.spyPct == -0.7)
    }
}

// MARK: - Settings

/// A tiny settings server: GET returns the current settings, PUT merges the
/// body and answers with them; an unknown model id is a 400.
nonisolated final class AnalysisSettingsServer: @unchecked Sendable {
    private let lock = NSLock()
    private var current: [String: Any] = ["model_id": "abc", "model_name": "Opus", "sigma": 2.5, "min_move_pct": 3.0]
    private let models: [[String: Any]] = [
        ["id": "abc", "name": "Opus", "provider": "claude-cli", "model": "claude-opus-5-5"],
        ["id": "def", "name": "Gemini", "provider": "openrouter", "model": "gemini-3-pro"],
    ]

    func handle(_ req: URLRequest) -> (status: Int, body: String) {
        lock.withLock {
            if req.method == "PUT" {
                let body = req.jsonBody
                if let id = body["model_id"].string, !models.contains(where: { ($0["id"] as? String) == id }) {
                    return (400, #"{"detail": "Unknown model_id"}"#)
                }
                if body.orderedObject?["model_id"] != nil {
                    let id = body["model_id"].string
                    current["model_id"] = id ?? NSNull()
                    current["model_name"] = models.first { ($0["id"] as? String) == id }?["name"] ?? NSNull()
                }
                if let s = body["sigma"].double { current["sigma"] = s }
                if let m = body["min_move_pct"].double { current["min_move_pct"] = m }
            }
            var out = current
            out["models"] = models
            let data = try! JSONSerialization.data(withJSONObject: out)
            return (200, String(decoding: data, as: UTF8.self))
        }
    }
}

@MainActor
struct AnalysisSettingsModelTests {
    private func setup(clock: ManualClock? = nil) -> (AnalysisSettingsModel, DataStub) {
        let server = AnalysisSettingsServer()
        let stub = DataStub()
        stub.handler = { server.handle($0) }
        let m = AnalysisSettingsModel(repository: { AnalysisRepository(client: stub.client) }, sleep: clock?.sleep ?? realPollingSleep)
        return (m, stub)
    }

    private func puts(_ stub: DataStub) -> [URLRequest] {
        stub.requests.filter { $0.method == "PUT" && $0.path == "/analysis/settings" }
    }

    @Test func loadsTheModelsAndChoosesNone() async throws {
        let (m, stub) = setup()
        await m.load()
        #expect(m.settings.value?.modelLabel == "Opus")
        #expect(m.settings.value?.models.map(\.id) == ["abc", "def"])

        await m.setModel(nil)
        #expect(puts(stub).map(\.jsonBody) == [["model_id": nil]])
        #expect(m.settings.value?.modelId == nil)
        #expect(m.settings.value?.modelLabel == "None")

        await m.setModel("def")
        #expect(puts(stub).last?.jsonBody == ["model_id": "def"])
        #expect(m.settings.value?.modelLabel == "Gemini")
        // Choosing the current model sends nothing.
        await m.setModel("def")
        #expect(puts(stub).count == 2)
    }

    @Test func thresholdsSaveOnlyWhatChanged() async throws {
        let (m, stub) = setup()
        await m.load()
        m.setSigma(3.0)
        #expect(m.settings.value?.sigma == 3.0) // shown at once
        await m.flush()
        #expect(puts(stub).map(\.jsonBody) == [["sigma": 3.0]])

        m.setMinMovePct(4.5)
        await m.flush()
        #expect(puts(stub).last?.jsonBody == ["min_move_pct": 4.5])
        #expect(m.settings.value?.minMovePct == 4.5)
        #expect(m.error == nil)
    }

    @Test func thresholdsAreClampedToTheServersRanges() async throws {
        let (m, stub) = setup()
        await m.load()
        m.setSigma(9)
        m.setMinMovePct(0.1)
        await m.flush()
        #expect(puts(stub).last?.jsonBody == ["sigma": 6.0, "min_move_pct": 0.5])
        #expect(AnalysisSettingsModel.clamp(30, AnalysisSettings.minMoveRange) == 25)
        #expect(AnalysisSettingsModel.clamp(2.54, AnalysisSettings.sigmaRange) == 2.5)
    }

    @Test func stepperTapsAreGatheredIntoOneSave() async throws {
        let clock = ManualClock()
        let (m, stub) = setup(clock: clock)
        await m.load()
        m.setSigma(3.0)
        m.setSigma(3.5)
        // Wait until the second tap's save is actually waiting on the clock;
        // advancing before it has started to sleep would never wake it.
        #expect(await eventually { clock.requested.count == 2 && clock.pendingCount == 1 })
        await clock.advance(by: .milliseconds(300))
        #expect(puts(stub).isEmpty)
        await clock.advance(by: AnalysisSettingsModel.saveDelay)
        #expect(await eventually { !puts(stub).isEmpty })
        #expect(await eventually { !m.saving })
        #expect(puts(stub).map(\.jsonBody) == [["sigma": 3.5]])
    }

    @Test func aRejectedSavePutsTheSavedValueBack() async throws {
        let stub = DataStub()
        let server = AnalysisSettingsServer()
        stub.handler = { req in
            req.method == "PUT" ? (400, #"{"detail": "sigma must be between 1.0 and 6.0"}"#) : server.handle(req)
        }
        let m = AnalysisSettingsModel(repository: { AnalysisRepository(client: stub.client) })
        await m.load()
        m.setSigma(4.0)
        await m.flush()
        #expect(m.error == "sigma must be between 1.0 and 6.0")
        #expect(m.settings.value?.sigma == 2.5)
    }

    @Test func anOlderServerReadsAsUnsupported() async {
        let stub = DataStub(status: 404, json: #"{"detail": "Not Found"}"#)
        let m = AnalysisSettingsModel(repository: { AnalysisRepository(client: stub.client) })
        await m.load()
        #expect(m.isUnsupported)
    }
}

// MARK: - Wire format and helpers

@MainActor
struct AnalysisRepositoryWireTests {
    @Test func everyEndpointSendsTheContractShape() async throws {
        let stub = DataStub(json: "{}")
        let repo = AnalysisRepository(client: stub.client)

        _ = try await repo.settings()
        #expect(stub.last?.method == "GET" && stub.last?.path == "/analysis/settings")

        _ = try await repo.updateSettings(AnalysisSettingsChange(modelId: .some("abc"), sigma: 3))
        #expect(stub.last?.method == "PUT" && stub.last?.path == "/analysis/settings")
        #expect(stub.last?.jsonBody == ["model_id": "abc", "sigma": 3.0])

        _ = try await repo.moves(symbol: "NVDA", range: .sixMonths)
        #expect(stub.last?.path == "/analysis/moves")
        #expect(stub.last?.queryItems == ["symbol": "NVDA", "range": "6M"])

        stub.respond(json: #"{"queued": 4}"#)
        let queued = try await repo.explain(symbol: "NVDA", range: .oneMonth, dates: nil, force: false)
        #expect(queued == 4)
        #expect(stub.last?.method == "POST" && stub.last?.path == "/analysis/moves/explain")
        #expect(stub.last?.jsonBody == ["symbol": "NVDA", "range": "1M", "force": false])

        // An Alpaca account's id rides along for news; blank is left out.
        _ = try await repo.explain(symbol: "NVDA", range: .threeMonths, dates: ["2026-10-02"], force: true, brokerageId: "acct-alpaca")
        #expect(stub.last?.jsonBody == ["symbol": "NVDA", "range": "3M", "dates": ["2026-10-02"], "force": true, "brokerage_id": "acct-alpaca"])
        _ = try await repo.explain(symbol: "NVDA", range: .threeMonths, dates: nil, force: false, brokerageId: "")
        #expect(stub.last?.jsonBody["brokerage_id"] == JSON.null)

        stub.respond(json: AnalysisFixtures.portfolio)
        _ = try await repo.portfolio(brokerageId: "acct-1")
        #expect(stub.last?.path == "/analysis/portfolio")
        #expect(stub.last?.queryItems == ["brokerage_id": "acct-1"])
    }
}

struct AnalysisFormatTests {
    @Test func familiesGroupTheElevenCategories() {
        for key in ["earnings", "guidance", "deal_product", "management", "capital_insider"] {
            #expect(AnalysisCategoryFamily(category: key) == .company)
        }
        #expect(AnalysisCategoryFamily(category: "analyst") == .street)
        #expect(AnalysisCategoryFamily(category: "sector_peer") == .street)
        #expect(AnalysisCategoryFamily(category: "macro") == .macro)
        #expect(AnalysisCategoryFamily(category: "regulatory_legal") == .macro)
        #expect(AnalysisCategoryFamily(category: "market") == .market)
        #expect(AnalysisCategoryFamily(category: "no_catalyst") == .noCatalyst)
        #expect(AnalysisCategoryFamily(category: "something_new") == .other)
        #expect(AnalysisCategoryFamily(category: nil) == .other)
    }

    @Test func markersAreHollowUntilExplained() throws {
        func style(_ status: String, _ category: String? = nil) throws -> AnalysisMarkerStyle {
            AnalysisMarkerStyle(MoveEvent(json: try AnalysisFixtures.json(AnalysisFixtures.event("2026-10-02", status: status, category: category, label: category))))
        }
        #expect(try style("unexplained") == .hollow)
        #expect(try style("pending") == .hollow)
        #expect(try style("failed") == .failed)
        #expect(try style("explained", "analyst") == .solid(.street))
        #expect(try style("explained", "market") == .solid(.market))
    }

    @Test func textHelpers() throws {
        #expect(AnalysisFormat.dayLabel("2026-10-02", currentYear: 2026) == "Oct 2")
        #expect(AnalysisFormat.dayLabel("2025-12-31", currentYear: 2026) == "Dec 31, 2025")
        #expect(AnalysisFormat.dayLabel("garbage", currentYear: 2026) == "garbage")
        // A no-break space keeps each label on its value's line.
        #expect(AnalysisFormat.splitText(market: -0.8, sector: -1.1, specific: -6) == "Market\u{00A0}-0.80% · Sector\u{00A0}-1.10% · Stock\u{00A0}-6.00%")
        #expect(AnalysisFormat.splitText(market: nil, sector: nil, specific: 2) == "Stock\u{00A0}+2.00%")
        #expect(AnalysisFormat.splitText(market: nil, sector: nil, specific: nil) == nil)
        #expect(AnalysisFormat.confidenceLabel("medium") == "Medium confidence")
        #expect(AnalysisFormat.confidenceLabel(nil) == nil)

        let m = MoveAnalysis(json: try AnalysisFixtures.json(AnalysisFixtures.moves))
        #expect(AnalysisFormat.profileLine(m) == "Technology · XLK · beta 1.62")
        #expect(AnalysisFormat.title(m.events[0]) == "Downgrade on export limits")
        #expect(AnalysisFormat.title(m.events[1]) == "Not explained yet")
        #expect(AnalysisFormat.metaLine(m.events[0], currentYear: 2026) == "Oct 2 · Analysts")
        #expect(AnalysisFormat.metaLine(m.events[1], currentYear: 2026) == "Sep 15")
        // The category is not repeated when the headline is the label.
        let none = MoveEvent(json: try AnalysisFixtures.json(#"{"symbol": "X", "date": "2026-03-04", "status": "explained", "category": "no_catalyst", "category_label": "No catalyst found", "headline": "No catalyst found"}"#))
        #expect(AnalysisFormat.metaLine(none, currentYear: 2026) == "Mar 4")
        #expect(AnalysisFormat.movesSubtitle(m) == "2 days stood out · 1 not explained")
        let change = try #require(AnalysisFormat.rangeChange(m.bars))
        #expect(close(change.abs, -8.6, 1e-9))
        #expect(close(change.pct, -8.6 / 210 * 100, 1e-9))
    }

    @Test func subtitleWhilePendingAndWhenEmpty() throws {
        let pending = MoveAnalysis(json: try AnalysisFixtures.json(AnalysisFixtures.moves(pending: 2, events: AnalysisModelTests.sixEvents)))
        #expect(AnalysisFormat.movesSubtitle(pending) == "6 days stood out · explaining 2")
        let none = MoveAnalysis(json: try AnalysisFixtures.json(AnalysisFixtures.moves(events: [])))
        #expect(AnalysisFormat.movesSubtitle(none) == "No day stood out in this range")
    }

    @Test func chartTapsSnapToANearbyMarker() {
        // 11 sessions in a 268 pt view: a 216 pt plot after the 52 pt label
        // column, so x = 8 + 20 * i.
        let width: CGFloat = 268
        #expect(AnalysisMoveChart.plotWidth(width) == 216)
        #expect(AnalysisMoveChart.x(0, count: 11, width: width) == 8)
        #expect(AnalysisMoveChart.x(10, count: 11, width: width) == 208)
        // No marker near: the nearest session.
        #expect(AnalysisMoveChart.index(at: 69, width: width, count: 11, markers: []) == 3)
        // A marker at session 4 (x 88) within 14 pt wins over session 3.
        #expect(AnalysisMoveChart.index(at: 76, width: width, count: 11, markers: [4]) == 4)
        // Too far from the marker: the nearest session.
        #expect(AnalysisMoveChart.index(at: 50, width: width, count: 11, markers: [4]) == 2)
        #expect(AnalysisMoveChart.index(at: 100, width: width, count: 1, markers: []) == 0)
        // A tap in the label column picks the last session.
        #expect(AnalysisMoveChart.index(at: 250, width: width, count: 11, markers: []) == 10)
    }
}

// MARK: - Fixes from the review against the real backend

@MainActor
struct AnalysisBackendReviewTests {
    private func model(_ stub: DataStub, symbol: String? = "NVDA") -> AnalysisModel {
        AnalysisModel(symbol: symbol, repository: { AnalysisRepository(client: stub.client) })
    }

    nonisolated private static func movesRequests(_ stub: DataStub?) -> Int {
        stub?.requests.filter { $0.method == "GET" && $0.path == "/analysis/moves" }.count ?? 0
    }

    // MARK: 1 and 7. Sector keys, missing beta

    @Test func sectorKeysReadAsWordsAndAMissingBetaIsLeftOut() throws {
        #expect(AnalysisFormat.sectorLabel("financial_services") == "Financial services")
        #expect(AnalysisFormat.sectorLabel("nasdaq_100") == "Nasdaq 100")
        #expect(AnalysisFormat.sectorLabel("small_caps") == "Small caps")
        #expect(AnalysisFormat.sectorLabel("technology") == "Technology")
        #expect(AnalysisFormat.sectorLabel("") == "")

        let qqq = MoveAnalysis(json: try AnalysisFixtures.json(#"{"symbol": "TQQQ", "range": "3M", "sector": "nasdaq_100", "sector_etf": "QQQ", "beta": null, "model_configured": true, "pending": 0, "bars": [], "events": [], "categories": []}"#))
        #expect(AnalysisFormat.profileLine(qqq) == "Nasdaq 100 · QQQ")
        let bank = MoveAnalysis(json: try AnalysisFixtures.json(#"{"symbol": "JPM", "range": "3M", "sector": "financial_services", "sector_etf": "XLF", "beta": 1.08, "model_configured": true, "pending": 0, "bars": [], "events": [], "categories": []}"#))
        #expect(AnalysisFormat.profileLine(bank) == "Financial services · XLF · beta 1.08")
    }

    // MARK: 2. Old server

    @Test func aBareNotFoundMeansTheServerHasNoAnalysisYet() async {
        let stub = DataStub(status: 404, json: #"{"detail": "Not Found"}"#)
        let m = model(stub)
        await m.load()
        #expect(m.isServerUnsupported)
        #expect(!m.isNotFound)
        #expect(!AnalysisFormat.isRetryable(m.moves.error))

        await m.loadAccount("acct-1", isStockAccount: true)
        #expect(m.isPortfolioUnsupported)
    }

    @Test func theBackendsUnknownSymbol404IsNotFound() async {
        let stub = DataStub(status: 404, json: #"{"detail": "No price history for ZZZZ."}"#)
        let m = model(stub, symbol: "ZZZZ")
        await m.load()
        #expect(m.isNotFound)
        #expect(!m.isServerUnsupported)
        await m.loadAccount("acct-1", isStockAccount: true)
        #expect(!m.isPortfolioUnsupported)
    }

    @Test func explainOnAnOldServerSaysSo() async {
        let stub = DataStub()
        stub.handler = { req in
            req.method == "POST" ? (404, #"{"detail": "Not Found"}"#) : (200, AnalysisFixtures.moves(events: AnalysisModelTests.sixEvents))
        }
        let m = model(stub)
        await m.load()
        await m.explainAll()
        #expect(m.actionError == "This server has no AI analysis yet")
    }

    // MARK: 3. Stale responses

    /// Holds the first moves request open until the test answers it.
    private final class HeldRequest {
        var continuation: CheckedContinuation<MoveAnalysis, any Error>?
    }

    @Test func anOlderAnswerNeverReplacesANewerOne() async throws {
        // The first request (a poll) is held and would say nothing is
        // pending; the second (after an explain) says two are, and lands
        // first. URL stubs answer in order, so the requests are finished by
        // hand here.
        let idle = MoveAnalysis(json: try AnalysisFixtures.json(AnalysisFixtures.moves(pending: 0, events: AnalysisModelTests.sixEvents)))
        let busy = MoveAnalysis(json: try AnalysisFixtures.json(AnalysisFixtures.moves(pending: 2, events: AnalysisModelTests.sixEvents)))
        let held = HeldRequest()
        var calls = 0
        let m = AnalysisModel(
            symbol: "NVDA",
            repository: { AnalysisRepository(client: DataStub().client) },
            fetchMoves: { _, _ in
                calls += 1
                if calls == 1 {
                    return try await withCheckedThrowingContinuation { held.continuation = $0 }
                }
                return busy
            }
        )

        let older = Task { await m.load() }
        #expect(await eventually { held.continuation != nil })
        await m.load()
        #expect(m.analysis?.pending == 2)

        held.continuation?.resume(returning: idle)
        await older.value
        #expect(calls == 2)
        // The late, older answer was dropped: still polling.
        #expect(m.analysis?.pending == 2)
        #expect(m.isPolling)
    }

    // MARK: 4. Symbols

    @Test func theServersSpellingIsAdoptedWithoutAnotherFetch() async {
        let stub = DataStub(json: AnalysisFixtures.moves(symbol: "BRK-B", events: []))
        let m = model(stub, symbol: "brk.b")
        let key = m.loadKey
        await m.load()
        #expect(stub.last?.queryItems["symbol"] == "BRK.B")
        #expect(m.symbol == "BRK-B")
        // Canonical either way, so the view does not fetch again.
        #expect(m.loadKey == key)

        // The same symbol, spelled the dashboard's way, changes nothing.
        m.select(symbol: "BRK.B")
        #expect(m.analysis != nil)
        #expect(Self.movesRequests(stub) == 1)

        // One chip for it, and the holding row highlights.
        m.setQuickPicks(["BRK.B", "AAPL", "BRK-B"])
        #expect(m.quickPicks == ["BRK.B", "AAPL"])
        #expect(!m.showsSymbolChip)
        #expect(m.isCurrent("BRK.B") && m.isCurrent("BRK-B"))
        #expect(!m.isCurrent("AAPL"))
    }

    @Test func aDifferentSymbolIsNeverAdopted() async {
        let stub = DataStub(json: AnalysisFixtures.moves(symbol: "NVDA", events: []))
        let m = model(stub, symbol: "AAPL")
        await m.load()
        #expect(m.symbol == "AAPL")
        #expect(AnalysisFormat.canonical("brk/b") == "BRK-B")
    }

    @Test func aSymbolNotInTheHoldingsGetsItsOwnChip() async {
        let stub = DataStub(json: AnalysisFixtures.moves(symbol: "NVDA", events: []))
        let m = model(stub)
        m.setQuickPicks(["AAPL"])
        #expect(m.showsSymbolChip)
        #expect(AnalysisFormat.canonical(" brk.b ") == "BRK-B")
    }

    // MARK: 5. Crypto

    @Test func cryptoIsNeverAQuickPickOrTheDefault() {
        let m = model(DataStub(), symbol: nil)
        m.setQuickPicks(["BTCUSD", "BTC/USD", "eth-usd", "DOGEUSDT", "GLD", "AAPL"])
        #expect(m.quickPicks == ["GLD", "AAPL"])
        #expect(m.symbol == "GLD")
    }

    @Test func cryptoSymbolsAndSearchResults() {
        for crypto in ["BTCUSD", "btc/usd", "ETH-USD", "SOLUSDC", "DOGEUSDT"] {
            #expect(AnalysisFormat.isCryptoSymbol(crypto), "\(crypto)")
        }
        // Stock tickers top out at five letters, so none of these is a pair.
        for stock in ["AAPL", "BRK.B", "BRK-B", "TQQQ", "GLD", "BIL", "ABUSD"] {
            #expect(!AnalysisFormat.isCryptoSymbol(stock), "\(stock)")
        }
        let results = [
            SearchInstrument(symbol: "BTC-USD", name: "Bitcoin USD", type: "Crypto"),
            SearchInstrument(symbol: "AAPL", name: "Apple Inc.", type: "Stock"),
            SearchInstrument(symbol: "QQQ", name: "Invesco QQQ Trust", type: "ETF"),
            SearchInstrument(symbol: "XRPUSD", name: "XRP", type: "Stock"),
        ]
        #expect(AnalysisFormat.analyzableResults(results).map(\.symbol) == ["AAPL", "QQQ"])
    }

    @Test func aBadRequestOffersNoRetry() {
        #expect(!AnalysisFormat.isRetryable(ApiError(message: "range must be one of 1M, 3M, 6M, 1Y", statusCode: 400)))
        #expect(AnalysisFormat.isRetryable(ApiError(message: "boom", statusCode: 500)))
        #expect(AnalysisFormat.isRetryable(ApiError(message: "No price history for ZZZZ.", statusCode: 404)))
        #expect(!AnalysisFormat.isRetryable(ApiError(message: "Not Found", statusCode: 404)))
        #expect(AnalysisFormat.isRetryable(URLError(.timedOut)))
    }

    // MARK: 6. brokerage_id

    @Test func explainSendsTheAlpacaAccountForNews() async throws {
        let stub = DataStub()
        stub.handler = { req in
            if req.method == "POST" { return (200, #"{"queued": 1}"#) }
            if req.path == "/analysis/portfolio" { return (200, AnalysisFixtures.portfolio) }
            return (200, AnalysisFixtures.moves(events: AnalysisModelTests.sixEvents))
        }
        let m = model(stub)
        await m.loadAccount("acct-alpaca", isStockAccount: true, isAlpaca: true)
        #expect(m.newsAccountId == "acct-alpaca")
        await m.load()
        await m.explainAll()
        let first = try #require(stub.requests.last { $0.method == "POST" })
        #expect(first.jsonBody == ["symbol": "NVDA", "range": "3M", "force": false, "brokerage_id": "acct-alpaca"])

        // A per-event Explain carries it too.
        let failed = try #require(m.event(id: "NVDA|2026-10-04"))
        await m.explain(failed)
        let second = try #require(stub.requests.last { $0.method == "POST" })
        #expect(second.jsonBody["brokerage_id"] == "acct-alpaca")

        // Any other kind of account sends none.
        await m.loadAccount("acct-other", isStockAccount: true, isAlpaca: false)
        #expect(m.newsAccountId == nil)
        await m.explainAll()
        let third = try #require(stub.requests.last { $0.method == "POST" })
        #expect(third.jsonBody == ["symbol": "NVDA", "range": "3M", "force": false])
    }

    // MARK: 8 and 9. Families and wording

    @Test func familiesMatchTheWebGrouping() {
        let groups: [AnalysisCategoryFamily: [String]] = [
            .company: ["earnings", "guidance", "deal_product", "management", "capital_insider"],
            .street: ["analyst", "sector_peer"],
            .macro: ["macro", "regulatory_legal"],
            .market: ["market"],
            .noCatalyst: ["no_catalyst"],
        ]
        for (family, keys) in groups {
            for key in keys { #expect(AnalysisCategoryFamily(category: key) == family, "\(key)") }
        }
    }

    @Test func aFailedEventsButtonSaysTryAgain() throws {
        func event(_ status: String) throws -> MoveEvent {
            MoveEvent(json: try AnalysisFixtures.json(AnalysisFixtures.event("2026-10-02", status: status, category: "analyst", label: "Analysts")))
        }
        #expect(AnalysisFormat.explainButtonTitle(try event("failed")) == "Try again")
        #expect(AnalysisFormat.explainButtonTitle(try event("explained")) == "Re-run")
        #expect(AnalysisFormat.explainButtonTitle(try event("unexplained")) == "Explain")
        // Failed is a red outline; unexplained and pending are hollow grey.
        #expect(AnalysisMarkerStyle(try event("failed")) == .failed)
        #expect(AnalysisMarkerStyle(try event("pending")) == .hollow)
    }
}
