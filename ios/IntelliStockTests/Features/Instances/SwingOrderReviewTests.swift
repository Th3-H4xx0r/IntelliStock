import CoreGraphics
import Testing
@testable import IntelliStock

/// The order review sends only on a drag that reaches the very top.
struct SwingOrderReviewTests {
    @Test func onlyADragToTheTopSends() {
        let h: CGFloat = 800
        // Up from the hint to the top tenth: sends.
        #expect(SwipeUpHint.reachesTop(fingerY: 60, travel: 680, height: h))
        // 90% of the way (finger stops at 110 pt, just below the top tenth): does not.
        #expect(!SwipeUpHint.reachesTop(fingerY: 110, travel: 630, height: h))
        // A short drag that starts near the top: does not.
        #expect(!SwipeUpHint.reachesTop(fingerY: 40, travel: 120, height: h))
        // No layout yet: never.
        #expect(!SwipeUpHint.reachesTop(fingerY: 0, travel: 800, height: 0))
    }

    @Test func aPutPlanReadsAsCollectOrAssigned() throws {
        let plan = try #require(SwingPutPlan(SwingOrderReview.demo().signal))
        #expect(plan.summary == "Collect about $131.00 if QCOM stays above $177.50 by Oct 9, 2026.")
        #expect(plan.assignment == "Below $177.50 you buy 100 shares at $177.50.")
        #expect(plan.breakeven == 177.5 - 1.31)
    }

    /// The swipe hint names the order, as the web's confirm button does.
    @Test func theHintNamesTheOrder() {
        let put = SwingOrderReview.demo().signal
        #expect(swingReviewActionLabel(put, "approve") == "Approve sell 1 QCOM put")
        let swing = SwingSignal(json: ["id": "a1", "lane": "swing", "symbol": "AMD", "proposal": ["shares": 12]])
        #expect(swingReviewActionLabel(swing, "approve") == "Approve buy 12 AMD")
        #expect(swingReviewActionLabel(swing, "approve_half") == "Approve ½ · AMD")
        let unsized = SwingSignal(json: ["id": "a2", "lane": "swing", "symbol": "AMD", "proposal": [:]])
        #expect(swingReviewActionLabel(unsized, "approve") == "Approve buy AMD")
    }

    /// Long reasoning collapses on a word boundary, short reasoning does not.
    @Test func reasoningCollapsesOnAWord() {
        let short = swingReasoningPreview("Short and sweet.")
        #expect(short.text == "Short and sweet.")
        #expect(!short.truncated)
        let long = String(repeating: "word ", count: 80)
        let preview = swingReasoningPreview(long)
        #expect(preview.truncated)
        #expect(preview.text.hasSuffix("word…"))
        #expect(preview.text.count <= swingReasoningCollapseChars + 1)
    }
}
