import Foundation
import Testing
@testable import IntelliStock

/// The approvals list: one read of every recent request, split into the
/// ones waiting for the operator and the rest, polled every 15 s. No
/// network: the source is scripted.
@Suite(.serialized)
struct ApprovalsListModelTests {
    private func make(_ source: FakeApprovalsSource, now: Date = kT0) -> ApprovalsListModel {
        ApprovalsListModel(source: { source }, clock: { now })
    }

    @Test func splitsWaitingFromRecentAndOrdersThem() {
        let soon = approvalA(expiresAt: kT0 + 60), later = approvalA(expiresAt: kT0 + 600)
        let lapsed = approvalA(expiresAt: kT0 - 1)                                    // created 06:00
        let done = McpApproval(json: ["id": "ap_d", "status": "executed", "created_at": "2026-11-19T05:59:00Z"])
        let old = McpApproval(json: ["id": "ap_o", "status": "denied", "created_at": "2026-11-19T05:00:00Z"])
        let (waiting, recent) = splitApprovals([later, old, lapsed, done, soon], now: kT0)
        #expect(waiting.map(\.expiresAt) == [kT0 + 60, kT0 + 600])                    // soonest expiry first
        #expect(recent.map(\.id) == ["ap_7f3c", "ap_d", "ap_o"])                     // newest first
    }

    @Test func anUndatedRequestGoesLast() {
        let undated = McpApproval(json: ["id": "ap_u", "status": "failed"])
        let done = McpApproval(json: ["id": "ap_d", "status": "executed", "created_at": "2026-11-19T05:59:00Z"])
        #expect(splitApprovals([undated, done], now: kT0).recent.map(\.id) == ["ap_d", "ap_u"])
    }

    @Test func loadAsksForAllWithLimit50() async throws {
        let source = FakeApprovalsSource(rows: [approvalA()])
        let m = make(source)
        await m.load()
        #expect(source.listCalls.count == 1)
        let call = try #require(source.listCalls.first)
        #expect(call.0 == .all && call.1 == 50)
        #expect(m.state.value?.waiting.map(\.id) == ["ap_7f3c"])
        #expect(m.state.value?.recent.isEmpty == true)
    }

    @Test func aFailedRefreshKeepsTheLastListAndSaysSo() async {
        let source = FakeApprovalsSource(rows: [approvalA()])
        let m = make(source)
        await m.load()
        source.listError = ApiError(message: "Cannot reach the server. Check your connection.")
        await m.refresh()
        #expect(m.state.value?.waiting.map(\.id) == ["ap_7f3c"])
        #expect(m.state.value?.refreshError == "Cannot reach the server. Check your connection.")
        source.listError = nil
        await m.refresh()
        #expect(m.state.value?.refreshError == nil)                                   // a good read clears it
    }

    @Test func aServerWithoutApprovalsSaysSoOnTheList() async {
        let source = FakeApprovalsSource()
        source.listError = ApiError(message: "Not Found", statusCode: 404)
        let m = make(source)
        await m.load()
        #expect(m.state.error.map(approvalsErrorText) == kApprovalsNotOnServer)
    }

    @Test func pollsEvery15Seconds() async {
        let source = FakeApprovalsSource(rows: [approvalA()])
        let m = make(source)
        let manual = ManualClock()
        let task = Task { await m.poll(lifecycle: nil, sleep: manual.sleep) }
        #expect(await eventually { source.listCalls.count == 1 && manual.pendingCount == 1 })
        #expect(manual.requested.last == ApprovalsListModel.pollEvery)
        await manual.advance(by: .seconds(14))
        #expect(source.listCalls.count == 1)
        await manual.advance(by: .seconds(1))
        #expect(await eventually { source.listCalls.count == 2 })
        task.cancel()
    }

    @Test func approvalRoutesParseAndPrint() {
        #expect(Route(path: "/approvals") == .approvals)
        #expect(Route.approvals.path == "/approvals")
        #expect(Route(path: "/approvals/ap_7f3c") == .approval("ap_7f3c"))
        #expect(Route.approval("ap_7f3c").path == "/approvals/ap_7f3c")
        #expect(Route(path: "/approvals/a/b") == nil)
    }
}
