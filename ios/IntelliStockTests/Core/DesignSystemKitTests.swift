import SwiftUI
import Testing
@testable import IntelliStock

/// The logic in the Wave R1 component kit (spec 2026-10-02, G2).
struct DesignSystemKitTests {
    // MARK: ChangeDirection

    @Test func changeDirectionFollowsTheSignWithZeroAsUp() {
        #expect(ChangeDirection(12.5) == .up)
        #expect(ChangeDirection(0) == .up) // pnl >= 0 is green everywhere in the app
        #expect(ChangeDirection(-0.01) == .down)
        #expect(ChangeDirection(nil) == .flat)
        #expect(ChangeDirection(.nan) == .flat)
    }

    @Test func changeDirectionDrawsArrowsAndColours() {
        #expect(ChangeDirection.up.systemImage == "arrow.up.right")
        #expect(ChangeDirection.down.systemImage == "arrow.down.right")
        #expect(ChangeDirection.flat.systemImage == nil)
        #expect(ChangeDirection.up.color == DS.Palette.up)
        #expect(ChangeDirection.down.color == DS.Palette.down)
        #expect(ChangeDirection.flat.color == Color.secondary)
        #expect(ChangeDirection.up.accessibilityPrefix == "Up")
        #expect(ChangeDirection.flat.accessibilityPrefix.isEmpty)
    }

    // MARK: StatGrid

    @Test func statGridClampsColumnsAndDropsOneAtAccessibilitySizes() {
        #expect(StatGrid<EmptyView>.effectiveColumns(2, .large) == 2)
        #expect(StatGrid<EmptyView>.effectiveColumns(3, .xxxLarge) == 3)
        #expect(StatGrid<EmptyView>.effectiveColumns(7, .large) == 3)
        #expect(StatGrid<EmptyView>.effectiveColumns(0, .large) == 1)
        #expect(StatGrid<EmptyView>.effectiveColumns(3, .accessibility1) == 2)
        #expect(StatGrid<EmptyView>.effectiveColumns(2, .accessibility3) == 1)
        #expect(StatGrid<EmptyView>.effectiveColumns(1, .accessibility5) == 1)
    }

    // MARK: Badges

    @Test func badgeLabelsAreSentenceCasedNotUpperCased() {
        #expect("real money".dsSentenceCased == "Real money")
        #expect("running".dsSentenceCased == "Running")
        #expect("approved ½".dsSentenceCased == "Approved ½")
        #expect("AI".dsSentenceCased == "AI")
        #expect("24/7".dsSentenceCased == "24/7")
        #expect("".dsSentenceCased == "")
    }

    // MARK: Rings and sparklines

    /// The ring moved into the design system; its label must stay the Dart's.
    @Test func allocationRingLabelMatchesTheDashboardFormat() {
        for fraction in [-0.2, 0, 0.004, 0.0099, 0.01, 0.125, 0.5, 1, 1.4] {
            #expect(AllocationRing.percentLabel(fraction) == DashboardFormat.allocationLabel(fraction))
        }
        #expect(AllocationRing.percentLabel(0) == "0%")
        #expect(AllocationRing.percentLabel(0.004) == "<1%")
        #expect(AllocationRing.percentLabel(0.125) == "13%")
    }

    @Test func sparklineIsUpWhenItEndsAtOrAboveItsStart() {
        #expect(Sparkline.isUp([1, 3, 2]))
        #expect(Sparkline.isUp([2, 1, 2]))
        #expect(!Sparkline.isUp([2, 3, 1.9]))
        #expect(Sparkline.isUp([]))
    }

    // MARK: Toolbar

    @Test func toolbarConventionSymbols() {
        #expect(ToolbarSymbol.add == "plus")
        // The glass toolbar draws the circle; the symbol must not draw another.
        #expect(ToolbarSymbol.more == "ellipsis")
    }

    @Test func cardTokensFollowTheSpec() {
        #expect(DS.cardPadding == 16)
        #expect(DS.cardGroupSpacing == 12)
        #expect(DS.Radius.card == 22)
    }
}
