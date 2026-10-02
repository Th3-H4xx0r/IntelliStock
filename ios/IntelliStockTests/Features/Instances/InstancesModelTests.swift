import Foundation
import Testing
@testable import IntelliStock

// Ported from test/features/instances/instances_filter_test.dart and
// pinned_instances_test.dart, plus the controllers' wire behaviour and the
// pending-signals section / wheel card logic.

private func inst(_ id: String, createdBy: String = "user", runCommand: Bool = false) -> Instance {
    Instance(id: id, name: id, createdBy: createdBy, runCommand: runCommand)
}

struct InstancesStateTests {
    private let user1 = inst("u1")
    private let ai1 = inst("a1", createdBy: "ai")
    private let user2 = inst("u2", runCommand: true)

    @Test func filterAllReturnsEverything() {
        let s = InstancesState(instances: [user1, ai1, user2], filter: .all)
        #expect(s.filtered == [user1, ai1, user2])
    }

    @Test func filterUserReturnsOnlyUserCreated() {
        let s = InstancesState(instances: [user1, ai1, user2], filter: .user)
        #expect(s.filtered == [user1, user2])
    }

    @Test func filterAiReturnsOnlyAiCreated() {
        #expect(InstancesState(instances: [user1, ai1, user2], filter: .ai).filtered == [ai1])
        #expect(InstancesState(instances: [], filter: .user).filtered.isEmpty)
    }

    @Test func countsSumToAll() {
        let s = InstancesState(instances: [inst("u1"), inst("u2"), inst("a1", createdBy: "ai")])
        #expect(s.allCount == 3)
        #expect(s.userCount == 2)
        #expect(s.aiCount == 1)
        #expect(s.userCount + s.aiCount == s.allCount)
    }

    @Test func instanceParsing() {
        let minimal = Instance(json: ["id": "test-1", "run_command": false])
        #expect(minimal.id == "test-1")
        #expect(minimal.createdBy == "user")
        #expect(!minimal.runCommand)
        #expect(minimal.stocks.isEmpty)
        #expect(Instance(json: ["id": "x", "run_command": true]).runCommand)
        #expect(Instance(json: ["id": "x", "stocks": ["AAPL", "TSLA"]]).stocks == ["AAPL", "TSLA"])
        #expect(Instance(json: ["id": "x", "stocks": [["symbol": "MSFT"], ["symbol": "NVDA"]]]).stocks == ["MSFT", "NVDA"])
        let empty = Instance(json: [:])
        #expect(empty.id == "")
        #expect(empty.name == "")
        #expect(empty.strategyId == nil && empty.brokerageId == nil)
        #expect(empty.granularityTimeIncrement == nil && empty.maxUsage == nil && empty.uptimeSeconds == nil)
        #expect(Instance(json: ["id": "x", "granularity_time_increment": 300]).granularityTimeIncrement == 300)
        #expect(Instance(json: ["id": "x", "granularity": "3600"]).granularityTimeIncrement == 3600)
    }

    @Test func backtestRowParsing() {
        let bt = InstanceBacktestRow(json: [
            "id": "bt-1", "stocks": ["AAPL"], "start_date": "2025-01-01", "end_date": "2025-06-01",
            "status": "completed", "pnl": 1234.56, "pnl_percent": 12.34, "time_elapsed_seconds": 300.0,
        ])
        #expect(bt.id == "bt-1" && bt.stocks == ["AAPL"] && bt.status == "completed")
        #expect(close(bt.pnl, 1234.56, 0.01))
        #expect(close(bt.pnlPercent, 12.34, 0.01))
        let queued = InstanceBacktestRow(json: ["id": "bt-2", "status": "queued"])
        #expect(queued.pnl == nil && queued.pnlPercent == nil)
    }

    @Test func stateCopiesAreIndependent() {
        let s = InstancesState(filter: .all, errorMessage: "oops")
        var s2 = s
        s2.filter = .ai
        s2.errorMessage = nil
        #expect(s.filter == .all && s2.filter == .ai)
        #expect(s.errorMessage == "oops" && s2.errorMessage == nil)
        var s3 = InstancesState(busyIds: ["a", "b"])
        s3.busyIds.insert("c")
        #expect(s3.busyIds.isSuperset(of: ["a", "c"]))
    }
}

struct PinnedInstancesTests {
    @Test func floatsPinnedToTheTopPreservingEachGroupOrder() {
        let out = sortPinnedFirst([inst("a"), inst("b"), inst("c"), inst("d")], ["c", "a"])
        #expect(out.map(\.id) == ["a", "c", "b", "d"])
    }

    @Test func unchangedWhenNothingIsPinned() {
        let items = [inst("a"), inst("b")]
        #expect(sortPinnedFirst(items, []) == items)
    }

    @Test func ignoresUnknownIdsAndKeepsOrderWhenAllPinned() {
        #expect(sortPinnedFirst([inst("a"), inst("b")], ["zzz"]).map(\.id) == ["a", "b"])
        #expect(sortPinnedFirst([inst("a"), inst("b")], ["a", "b"]).map(\.id) == ["a", "b"])
    }

    @Test func persistsAJsonArrayStringInInsertionOrder() {
        let store = InMemorySecureStorage()
        let model = PinnedInstancesModel(store: store)
        model.toggle("x")
        model.toggle("y")
        #expect(store.read("pinned_instances") == #"["x","y"]"#)
        model.toggle("x")
        #expect(store.read("pinned_instances") == #"["y"]"#)
        #expect(PinnedInstancesModel(store: store).pinned == ["y"])
    }

    @Test func toleratesGarbageInStorage() {
        let store = InMemorySecureStorage()
        try? store.write("pinned_instances", "{not json")
        #expect(PinnedInstancesModel(store: store).pinned.isEmpty)
        try? store.write("pinned_instances", #"{"a": 1}"#)
        #expect(PinnedInstancesModel(store: store).pinned.isEmpty)
    }
}

struct InstancesModelTests {
    @Test func startMarksBusyThenRefreshes() async {
        let stub = DataStub(json: #"{"instances": [{"id": "i1", "run_command": true}]}"#)
        let model = InstancesModel(repository: { InstanceRepository(client: stub.client) })
        await model.refreshNow()
        await model.start("i1")
        #expect(stub.requests.map { "\($0.method) \($0.path)" }.suffix(2) == ["POST /instances/i1/start", "GET /instances"])
        #expect(model.value?.busyIds.isEmpty == true)
    }

    @Test func anApiErrorLandsInErrorMessageAndClearsBusy() async {
        let stub = DataStub()
        stub.handler = { req in
            req.path == "/instances" ? (200, #"{"instances": [{"id": "i1"}]}"#) : (409, #"{"detail": "already running"}"#)
        }
        let model = InstancesModel(repository: { InstanceRepository(client: stub.client) })
        await model.refreshNow()
        model.setFilter(.ai)
        await model.stop("i1")
        #expect(model.value?.errorMessage == "already running")
        #expect(model.value?.busyIds.isEmpty == true)
        // A refetch keeps the filter and the message.
        await model.refreshNow()
        #expect(model.value?.filter == .ai)
        #expect(model.value?.errorMessage == "already running")
    }

    @Test func aFailedRefreshIsTheErrorState() async {
        let stub = DataStub(status: 500, json: #"{"detail": "down"}"#)
        let model = InstancesModel(repository: { InstanceRepository(client: stub.client) })
        await model.refreshNow()
        #expect(model.state.errorMessage == "down")
    }

    @Test func pollsEveryThirtySeconds() async {
        let stub = DataStub(json: #"{"instances": []}"#)
        let clock = ManualClock()
        let model = InstancesModel(repository: { InstanceRepository(client: stub.client) })
        let task = Task { await model.poll(lifecycle: nil, sleep: clock.sleep) }
        #expect(await eventually { model.value != nil })
        await clock.advance(by: .seconds(1))
        #expect(clock.requested.first == .seconds(30))
        task.cancel()
    }
}

struct InstanceDetailModelTests {
    private func detailStub(backtests: String = #"[{"id": "b1", "status": "running"}, {"id": "b2", "status": "completed"}]"#) -> DataStub {
        let stub = DataStub()
        stub.handler = { req in
            switch req.path {
            case "/instances/i1": return (200, #"{"id": "i1", "run_command": true, "uptime_seconds": 10}"#)
            case "/instances/i1/backtests": return (200, #"{"backtests": \#(backtests), "total": 2, "total_pages": 3}"#)
            case "/backtests/b1/status": return (200, #"{"progress": 42.9, "status": "running"}"#)
            default: return (200, "{}")
            }
        }
        return stub
    }

    @Test func loadReadsTheInstanceAndTheFirstPage() async {
        let stub = detailStub()
        let model = InstanceDetailModel(instanceId: "i1", repository: { InstanceRepository(client: stub.client) })
        await model.load()
        let v = model.value!
        #expect(v.instance?.id == "i1")
        #expect(v.backtests.map(\.id) == ["b1", "b2"])
        #expect(v.btTotal == 2 && v.btTotalPages == 3 && v.btPage == 1)
        #expect(v.liveUptimeSecs == 10)
        let bt = stub.requests.first { $0.path == "/instances/i1/backtests" }
        #expect(bt?.queryItems == ["page": "1", "per_page": "15", "sort_by": "completed_at", "sort_order": "desc"])
    }

    @Test func uptimeTicksOnlyWhileRunning() async {
        let stub = detailStub()
        let model = InstanceDetailModel(instanceId: "i1", repository: { InstanceRepository(client: stub.client) })
        await model.load()
        model.tickUptime()
        #expect(model.value?.liveUptimeSecs == 11)
    }

    @Test func progressPollStoresProgressAndOnlyRunningRowsArePolled() async {
        let stub = detailStub()
        let model = InstanceDetailModel(instanceId: "i1", repository: { InstanceRepository(client: stub.client) })
        await model.load()
        #expect(model.hasRunningBacktests)
        await model.pollBtProgress()
        #expect(model.value?.btProgress == ["b1": 42])
        #expect(!stub.requests.contains { $0.path == "/backtests/b2/status" })
    }

    @Test func aTerminalStatusRefetchesThePage() async {
        let stub = detailStub()
        let model = InstanceDetailModel(instanceId: "i1", repository: { InstanceRepository(client: stub.client) })
        await model.load()
        let before = stub.requests.filter { $0.path == "/instances/i1/backtests" }.count
        stub.handler = { req in
            req.path == "/backtests/b1/status" ? (200, #"{"progress": 100, "status": "Completed"}"#) : (200, #"{"backtests": []}"#)
        }
        await model.pollBtProgress()
        #expect(stub.requests.filter { $0.path == "/instances/i1/backtests" }.count == before + 1)
        #expect(model.value?.backtests.isEmpty == true)
    }

    @Test func sortTogglesTheSameFieldAndANewFieldStartsDescending() async {
        let stub = detailStub()
        let model = InstanceDetailModel(instanceId: "i1", repository: { InstanceRepository(client: stub.client) })
        await model.load()
        await model.goToBacktestPage(2)
        await model.sortBacktests("completed_at")
        #expect(model.value?.btSortOrder == "asc")
        #expect(model.value?.btPage == 1)
        await model.sortBacktests("pnl")
        #expect(model.value?.btSortBy == "pnl" && model.value?.btSortOrder == "desc")
        #expect(stub.last?.queryItems["sort_by"] == "pnl")
    }

    @Test func toggleRunStopsARunningInstance() async {
        let stub = detailStub()
        let model = InstanceDetailModel(instanceId: "i1", repository: { InstanceRepository(client: stub.client) })
        await model.load()
        await model.toggleRun()
        #expect(stub.requests.contains { $0.method == "POST" && $0.path == "/instances/i1/stop" })
        #expect(stub.last?.path == "/instances/i1")
    }

    @Test func clearStateBodies() async throws {
        let stub = detailStub()
        let model = InstanceDetailModel(instanceId: "i1", repository: { InstanceRepository(client: stub.client) })
        await model.load()
        _ = try await model.previewClearState("lookback_only")
        #expect(stub.last?.jsonBody == ["scope": "lookback_only", "apply": false])
        _ = try await model.applyClearState("full_instance")
        #expect(stub.last?.jsonBody == ["scope": "full_instance", "apply": true, "confirm": "i1"])
    }

    @Test func labelsMatchTheDartHelpers() {
        #expect(instanceGranLabel(nil) == "—")
        #expect(instanceGranLabel(30) == "30s")
        #expect(instanceGranLabel(900) == "15m")
        #expect(instanceGranLabel(3600) == "1h")
        #expect(instanceGranLabel(86400) == "1d")
        #expect(instanceUptimeLabel(0) == "—")
        #expect(instanceUptimeLabel(3723) == "1h 2m 3s")
        #expect(instanceUptimeLabel(123) == "2m 3s")
        #expect(instanceUptimeLabel(5) == "5s")
        #expect(InstanceClearScope.successMessage(["total_deleted": 12, "tables": ["a", "b"]]) == "Deleted 12 row(s) across 2 table(s).")
        #expect(InstanceClearScope.successMessage([:]) == "Deleted 0 row(s) across 0 table(s).")
        #expect(InstanceClearScope.previewTotal(["total_deleted": 7]) == "Total rows to delete: 7")
        #expect(InstanceClearScope.all.map(\.value) == ["lookback_only", "strategy_cache_only", "full_instance"])
    }

    @Test func backtestFormValidation() {
        #expect(InstanceBacktestForm.validate(start: "", end: "x") == "Start date is required")
        #expect(InstanceBacktestForm.validate(start: "2026-01-01", end: "") == "End date is required")
        #expect(InstanceBacktestForm.validate(start: "2026-02-01", end: "2026-01-01") == "End date must be after start date")
        #expect(InstanceBacktestForm.validate(start: "2026-01-01", end: "2026-01-01") == "End date must be after start date")
        #expect(InstanceBacktestForm.validate(start: "2026-01-01", end: "2026-02-01") == nil)
        #expect(InstanceBacktestForm.stocks(" aapl, ,tsla ,") == ["AAPL", "TSLA"])
        #expect(InstanceBacktestForm.cash("abc") == 100_000)
        #expect(InstanceBacktestForm.cash(" 2500 ") == 2500)
    }

    @Test func selectorLabels() {
        #expect(instanceBrokerageLabel(["account_name": "Main", "brokerage_type": "alpaca"]) == "Main (alpaca)")
        #expect(instanceBrokerageLabel([:]) == "()")
        #expect(instanceStrategyLabel(["id": 7]) == "7")
        #expect(instanceStrategyLabel(["id": 7, "name": "EB"]) == "EB")
    }
}

/// pending_signals_section_test.dart + wheel card (behaviour).
struct PendingSignalsSectionLogicTests {
    @Test func wheelProposalShowsPremiumWithCreditAndCollateral() {
        let fields = swingProposalFields(wheelTestSignal("w1"))
        #expect(fields.map(\.label) == ["CONTRACT", "STRIKE", "EXPIRY", "QTY", "LIMIT", "PREMIUM", "COLLATERAL"])
        #expect(fields.map(\.value) == ["APH261002P00130000", "$130.00", "2026-10-02", "1", "$1.23", "$1.30 ($130.00)", "$13,000.00"])
    }

    @Test func swingProposalAndApproveHalfOnSwingOnly() {
        let s = swingTestSignal("a1")
        #expect(swingProposalFields(s).map(\.value) == ["$200.00", "$188.00", "$218.00", "6"])
        #expect(s.allowsHalf)
        #expect(!wheelTestSignal("w1").allowsHalf)
        #expect(s.keyRisksText == "earnings in 9 days")
        #expect(swingSessionText(s) == "session 2026-09-24")
    }

    @Test func uncertainCardCopyAndUpperCasedBadge() {
        let card = UncertainCard(signal: swingTestSignal("a1"), since: 1, sinceAt: Date())
        #expect(card.badge.uppercased() == "UNCERTAIN — WAITING FOR THE BROKER")
        #expect(swingUncertainCopy(card) == waitingCopy)
        #expect(swingUncertainCopy(card.settled("submitted")) == "The broker submitted it. Check open orders for the fill.")
        #expect(swingUncertainCopy(card.settled("failed")) == "It failed at the broker; the live log says why.")
    }

    @Test func wheelHelpers() {
        #expect(fmtItm(2.0) == "2.0% ITM")
        #expect(fmtItm(-3.0) == "3.0% OTM")
        #expect(fmtItm(nil) == "—")
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(secondsFromGMT: 0)!
        #expect(fmtAsOf(swingUTC(2026, 9, 25, 14, 2, 41), calendar: cal) == "as of 14:02")
        #expect(fmtAsOf(nil) == "")
        #expect(wheelErrorMessage(ApiError(message: "Not Found", statusCode: 404)) == "This API build has no wheel endpoint yet.")
        #expect(wheelErrorMessage(ApiError(message: "Instance not found: i1", statusCode: 404)) == "Instance not found: i1")
        #expect(wheelErrorMessage(ApiError(message: "broker unavailable", statusCode: 503)) == "broker unavailable")
    }

    @Test func wheelModelErrorsThenRetries() async {
        let repo = SwingFakeSource([])
        repo.wheelError = ApiError(message: "broker unavailable", statusCode: 503)
        let model = WheelModel(instanceId: "i1", source: { repo })
        await model.load()
        #expect(model.state.error != nil)
        repo.wheelError = nil
        await model.retry()
        #expect(model.state.value?.openPuts.isEmpty == true)
        #expect(repo.wheelCalls == 2)
    }

    @Test func monitorBuyBackRule() {
        #expect(WheelPut(contract: "", underlying: "", expiry: "", itmPct: 10, dte: 20).monitorWillBuyBack)
        #expect(WheelPut(contract: "", underlying: "", expiry: "", itmPct: 5, dte: 2).monitorWillBuyBack)
        #expect(!WheelPut(contract: "", underlying: "", expiry: "", itmPct: 2, dte: 8).monitorWillBuyBack)
    }
}
