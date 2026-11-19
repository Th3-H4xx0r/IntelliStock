import Foundation
import Testing
@testable import IntelliStock

/// What the approvals list and detail say: the countdown, the status and
/// money-state words and dots, and the parameters as text.
struct ApprovalPresentationTests {
    @Test(arguments: [(872.0, "Expires in 14:32"), (5.0, "Expires in 0:05"), (0.4, "Expires in 0:01"), (0.0, "Expired"), (-3.0, "Expired")])
    func expiryCountsDownAndNeverShowsZeroWhileOpen(remaining: Double, text: String) {
        #expect(expiryLabel(remaining: remaining) == text)
    }

    @Test func statusAndMoneyLabels() {
        let statuses: [(String, String, DSStatusTone)] = [
            ("pending", "Waiting for you", .warn),
            ("approved", "Approved · running", .live),
            ("executed", "Done", .positive),
            ("failed", "Failed", .error),
            ("denied", "Denied", .off),
            ("expired", "Expired", .off),
            ("weird", "weird", .off),
        ]
        for (status, label, tone) in statuses {
            #expect(approvalStatusLabel(status) == label, "status \(status)")
            #expect(approvalStatusTone(status) == tone, "status \(status)")
        }
        let moneyStates: [(String, String, DSStatusTone)] = [
            ("live", "Live money", .error),
            ("unknown", "Unknown account · treated as live", .warn),
            ("protected", "Protected paper target", .warn),
            ("irreversible", "Can't be undone", .error),
            ("weird", "weird", .off),
        ]
        for (state, label, tone) in moneyStates {
            #expect(moneyStateLabel(state) == label, "money_state \(state)")
            #expect(moneyStateTone(state) == tone, "money_state \(state)")
        }
    }

    @Test func aPendingRequestPastItsExpiryReadsExpired() {
        #expect(approvalDisplayStatus(approvalA(), now: kT0 + 899) == "pending")
        #expect(approvalDisplayStatus(approvalA(), now: kT0 + 900) == "expired")
        #expect(approvalDisplayStatus(approvalA(status: "executed"), now: kT0 + 9999) == "executed")
    }

    @Test func parametersArePrettyAndEmptyReadsNone() {
        #expect(approvalParametersText(["qty": 10, "symbol": "TQQQ"]) == "{\n  \"qty\": 10,\n  \"symbol\": \"TQQQ\"\n}")
        #expect(approvalParametersText(.null) == "None")
        #expect(approvalParametersText(.object(JSONObject())) == "None")
    }

    @Test func aLongLineIsSplitSoNoLineIsWiderThanTheLimit() {
        let long = String(repeating: "a", count: ApprovalJSONBlock.maxColumns * 2 + 5)
        let lines = ApprovalJSONBlock.lines("{\n  \"k\": \"\(long)\"\n}")
        #expect(lines.count == 5)
        #expect(lines.allSatisfy { $0.count <= ApprovalJSONBlock.maxColumns })
        #expect(lines.joined() == "{" + "  \"k\": \"\(long)\"" + "}")             // nothing dropped
        #expect(ApprovalJSONBlock.lines("a\n\nb") == ["a", "", "b"])                 // blank lines kept
    }
}
