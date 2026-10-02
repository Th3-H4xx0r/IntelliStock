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
}
