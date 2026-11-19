import Foundation
import Testing
@testable import IntelliStock

/// The sign-and-send state machine: Face ID first, then a fresh timestamp,
/// the contract message signed, one POST, and every way it can end. No
/// Secure Enclave, no prompt, no network.
@Suite(.serialized)
struct ApprovalDetailModelTests {
    let clock = ApprovalTestClock(kT0)

    private func model(_ source: FakeApprovalsSource, signer: FakeApprovalSigner = FakeApprovalSigner(),
                       device: ApprovalDeviceRecord? = kActiveDevice, id: String = "ap_7f3c") async -> ApprovalDetailModel {
        let store = ApprovalDeviceStore(storage: InMemorySecureStorage())
        if let device { try? store.save(device) }
        let clock = clock
        let m = ApprovalDetailModel(approvalId: id, source: { source }, signer: signer, devices: store,
                                    baseURL: { "https://api.test" }, clock: { clock.now })
        await m.load()
        return m
    }

    @Test func approveSignsTheContractMessageAfterFaceIDAndPostsIt() async throws {
        let source = FakeApprovalsSource(details: [approvalA()])
        source.decideResult = approvalA(status: "executed", resultStatus: 200)
        let signer = FakeApprovalSigner()
        let clock = clock
        signer.onAuthorize = { clock.advance(by: 5) }                 // Face ID took 5 s
        let m = await model(source, signer: signer)
        let result = await m.decide(.approve)
        #expect(result == ApprovalDecisionResult(outcome: .decided, message: "Approved. The request ran."))
        #expect(signer.reasons == ["Approve: BUY 10 TQQQ on alpaca-main — LIVE"])
        let ts = Int((kT0 + 5).timeIntervalSince1970)                  // taken after Face ID
        #expect(signer.messages == [Data("ap_7f3c|\(kVectorAHash)|approve|\(ts)".utf8)])
        let call = try #require(source.decideCalls.first)
        #expect(call.id == "ap_7f3c")
        #expect(call.body == ApprovalDecisionBody(decision: .approve, bodySha256: kVectorAHash, ts: ts, deviceId: "dev_1", signature: signer.signature))
        #expect(m.state.value?.status == "executed" && m.phase == .idle)
    }

    @Test func aTimeoutReReadsInsteadOfReportingFailure() async {
        let source = FakeApprovalsSource(details: [approvalA(), approvalA(status: "approved")])
        source.decideError = ApiError(message: "Request timed out. Check your connection and try again.", statusCode: nil)
        let m = await model(source)
        #expect(await m.decide(.approve) == ApprovalDecisionResult(outcome: .uncertain, message: kApprovalUncertain))
        #expect(m.state.value?.status == "approved")
        #expect(m.pollInterval == ApprovalDetailModel.runningPoll)
        #expect(source.decideCalls.count == 1)                          // no automatic retry
    }

    @Test func fingerprintMismatchBlocksApproveButAllowsDeny() async throws {
        let wrong = String(repeating: "0", count: 64)
        let source = FakeApprovalsSource(details: [approvalA(bodySha256: wrong)])
        source.decideResult = approvalA(status: "denied", bodySha256: wrong)
        let signer = FakeApprovalSigner()
        let m = await model(source, signer: signer)
        #expect(await m.decide(.approve) == ApprovalDecisionResult(outcome: .blocked, message: kApprovalFingerprintMismatch))
        #expect(signer.reasons.isEmpty)
        #expect(await m.decide(.deny).outcome == .decided)
        #expect(try #require(source.decideCalls.first).body.bodySha256 == wrong)   // the server's hash is what gets signed
    }

    @Test func loadComputesTheFingerprint() async {
        let matching = await model(FakeApprovalsSource(details: [approvalA()]))
        #expect(matching.fingerprintMatches)
        let wrong = await model(FakeApprovalsSource(details: [approvalA(bodySha256: String(repeating: "0", count: 64))]))
        #expect(!wrong.fingerprintMatches)
    }

    @Test func denyIsSignedToo() async throws {
        let source = FakeApprovalsSource(details: [approvalA()])
        source.decideResult = approvalA(status: "denied")
        let signer = FakeApprovalSigner()
        let m = await model(source, signer: signer)
        #expect(await m.decide(.deny) == ApprovalDecisionResult(outcome: .decided, message: "Denied. The request will not run."))
        #expect(signer.reasons == ["Deny: BUY 10 TQQQ on alpaca-main — LIVE"])
        let message = String(decoding: try #require(signer.messages.first), as: UTF8.self)
        #expect(message.hasSuffix("|deny|1795068000"))
        #expect(try #require(source.decideCalls.first).body.decision == .deny)
    }

    @Test func aDoubleTapSendsOneDecision() async {
        let source = FakeApprovalsSource(details: [approvalA()])
        source.decideResult = approvalA(status: "executed", resultStatus: 200)
        let gate = ApprovalTestGate()
        source.decideGate = gate
        let m = await model(source)
        let first = Task { await m.decide(.approve) }
        #expect(await eventually { m.phase == .sending(.approve) })
        #expect(await m.decide(.approve).outcome == .ignored)
        #expect(await m.decide(.deny).outcome == .ignored)
        gate.open()
        #expect(await first.value.outcome == .decided)
        #expect(source.decideCalls.count == 1)
        #expect(m.phase == .idle)
    }

    @Test func cancellingFaceIDSendsNothing() async {
        let source = FakeApprovalsSource(details: [approvalA()])
        let signer = FakeApprovalSigner()
        signer.authorizeError = .cancelled
        let m = await model(source, signer: signer)
        #expect(await m.decide(.approve) == ApprovalDecisionResult(outcome: .cancelled, message: ""))
        #expect(source.decideCalls.isEmpty)
        #expect(m.phase == .idle)
    }

    @Test func aChangedFaceIDOrAMissingKeySaysRegisterAgain() async {
        let source = FakeApprovalsSource(details: [approvalA()])
        let signer = FakeApprovalSigner()
        signer.signError = .keyInvalidated
        let m = await model(source, signer: signer)
        #expect(await m.decide(.approve) == ApprovalDecisionResult(outcome: .blocked, message: kApprovalKeyInvalidated))
        signer.authorizeError = .noKey
        #expect(await m.decide(.deny) == ApprovalDecisionResult(outcome: .blocked, message: kApprovalNoKey))
        #expect(source.decideCalls.isEmpty)
    }

    @Test func aSignerFailureNamesTheCause() async {
        let source = FakeApprovalsSource(details: [approvalA()])
        let signer = FakeApprovalSigner()
        signer.authorizeError = .unavailable
        let m = await model(source, signer: signer)
        #expect(await m.decide(.approve) == ApprovalDecisionResult(outcome: .blocked, message: "Couldn't sign the decision: no Secure Enclave"))
        signer.authorizeError = nil
        signer.signError = .failed("OSStatus -25293")
        #expect(await m.decide(.approve) == ApprovalDecisionResult(outcome: .blocked, message: "Couldn't sign the decision: OSStatus -25293"))
        #expect(source.decideCalls.isEmpty)
    }

    @Test func aLongSummaryIsCutInTheFaceIDReason() async {
        func pending(summary: String) -> McpApproval {
            var fields = ApprovalWireTests.full.objectValue
            fields["status"] = "pending"
            fields["summary"] = .string(summary)
            return McpApproval(json: .object(fields))
        }
        let signer = FakeApprovalSigner()
        signer.authorizeError = .cancelled
        let long = await model(FakeApprovalsSource(details: [pending(summary: String(repeating: "x", count: 151))]), signer: signer)
        _ = await long.decide(.approve)
        let exact = await model(FakeApprovalsSource(details: [pending(summary: String(repeating: "y", count: 150))]), signer: signer)
        _ = await exact.decide(.deny)
        #expect(signer.reasons == ["Approve: " + String(repeating: "x", count: 149) + "…", "Deny: " + String(repeating: "y", count: 150)])
    }

    @Test func alreadyDecided409ReReadsAndShowsTheRealStatus() async {
        let source = FakeApprovalsSource(details: [approvalA(), approvalA(status: "executed", resultStatus: 200)])
        source.decideError = ApiError(message: "not pending", statusCode: 409)
        let m = await model(source)
        let result = await m.decide(.approve)
        #expect(result == ApprovalDecisionResult(outcome: .notPending, message: "not pending"))
        #expect(m.state.value?.status == "executed")
        #expect(m.phase == .idle)
    }

    @Test func expired410ReReads() async {
        let source = FakeApprovalsSource(details: [approvalA(), approvalA(status: "expired")])
        source.decideError = ApiError(message: "The server responded with status 410.", statusCode: 410)   // no detail
        let m = await model(source)
        let result = await m.decide(.approve)
        #expect(result == ApprovalDecisionResult(outcome: .notPending, message: "This request is expired — there's nothing to decide."))
        #expect(m.state.value?.status == "expired")
    }

    @Test func badSignatureOrInactiveDevice403StaysPendingWithTheServerReason() async {
        let detail = "device dev_1 is not active until 2026-11-19T06:10:00Z"
        let source = FakeApprovalsSource(details: [approvalA()])
        source.decideError = ApiError(message: detail, statusCode: 403)
        let m = await model(source)
        #expect(await m.decide(.approve) == ApprovalDecisionResult(outcome: .rejected, message: detail))
        #expect(m.state.value?.status == "pending")
        #expect(m.phase == .idle)
    }

    @Test func a403Or400WithoutADetailGetsPlainCopy() async {
        let source = FakeApprovalsSource(details: [approvalA()])
        source.decideError = ApiError(message: "The server responded with status 403.", statusCode: 403)
        let m = await model(source)
        #expect(await m.decide(.approve) == ApprovalDecisionResult(outcome: .rejected, message: "This iPhone isn't an active approver."))
        source.decideError = ApiError(message: "", statusCode: 400)
        #expect(await m.decide(.approve) == ApprovalDecisionResult(outcome: .rejected, message: "The server refused the decision." + kApprovalClockHint))
    }

    @Test func clockSkew400AddsTheDateAndTimeHint() async {
        let source = FakeApprovalsSource(details: [approvalA()])
        source.decideError = ApiError(message: "ts outside window", statusCode: 400)
        let m = await model(source)
        #expect(await m.decide(.approve) == ApprovalDecisionResult(outcome: .rejected, message: "ts outside window" + kApprovalClockHint))
    }

    @Test func a5xxIsTreatedAsUncertain() async {
        let source = FakeApprovalsSource(details: [approvalA(), approvalA()])
        source.decideError = ApiError(message: "The server responded with status 502.", statusCode: 502)
        let m = await model(source)
        #expect(await m.decide(.approve) == ApprovalDecisionResult(outcome: .uncertain, message: kApprovalUncertain))
        #expect(m.gate(now: kT0).canApprove)                            // may decide again, with a fresh signature
        #expect(source.approvalCalls.count == 2)                        // the load, then the re-read
    }

    @Test func expiryDuringFaceIDSendsNothing() async {
        let source = FakeApprovalsSource(details: [approvalA()])
        let signer = FakeApprovalSigner()
        let clock = clock
        signer.onAuthorize = { clock.advance(by: 901) }
        let m = await model(source, signer: signer)
        #expect(await m.decide(.approve) == ApprovalDecisionResult(outcome: .blocked, message: kApprovalExpired))
        #expect(source.decideCalls.isEmpty)
        #expect(signer.messages.isEmpty)
    }

    @Test func noRegisteredDeviceBlocksBoth() async {
        let source = FakeApprovalsSource(details: [approvalA()])
        let signer = FakeApprovalSigner()
        let m = await model(source, signer: signer, device: nil)
        #expect(await m.decide(.approve) == ApprovalDecisionResult(outcome: .blocked, message: kApprovalRegisterFirst))
        #expect(await m.decide(.deny).outcome == .blocked)
        #expect(signer.reasons.isEmpty && source.decideCalls.isEmpty)
    }

    @Test func invalidIdNeverHitsTheNetwork() async {
        let source = FakeApprovalsSource(details: [approvalA()])
        let m = await model(source, id: "../brokerages")
        #expect(m.state.errorMessage == "Not a valid approval id.")
        #expect(source.approvalCalls.isEmpty)
        #expect(m.gate(now: kT0) == .closed(""))
        #expect(await m.decide(.deny) == ApprovalDecisionResult(outcome: .blocked, message: ""))
    }

    @Test func aFailedRefreshKeepsTheApprovalAndSaysWhy() async {
        let source = FakeApprovalsSource(details: [approvalA()])
        let m = await model(source)
        source.detailError = ApiError(message: "Cannot reach the server. Check your connection.", statusCode: nil)
        await m.refresh()
        #expect(m.state.value?.status == "pending")
        #expect(m.refreshError == "Cannot reach the server. Check your connection.")
        source.detailError = nil
        await m.refresh()
        #expect(m.refreshError == nil)
    }

    @Test func pollIntervalFollowsStatus() async {
        let source = FakeApprovalsSource(details: [approvalA(), approvalA(status: "approved"), approvalA(status: "executed")])
        let m = await model(source)
        #expect(m.pollInterval == .seconds(10))
        await m.refresh()
        #expect(m.pollInterval == .seconds(3))
        await m.refresh()
        #expect(m.pollInterval == .seconds(60))
    }

    @Test func pollRefreshesOnItsInterval() async {
        let source = FakeApprovalsSource(details: [approvalA(), approvalA(status: "denied")])
        let m = await model(source)
        let manual = ManualClock()
        let poll = Task { await m.poll(lifecycle: nil, sleep: manual.sleep) }
        #expect(await eventually { manual.pendingCount == 1 })
        await manual.advance(by: .seconds(10))
        #expect(await eventually { m.state.value?.status == "denied" })
        #expect(manual.requested.first == .seconds(10))
        poll.cancel()
        await poll.value
    }
}
