import SwiftUI
import UIKit
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
        #expect(ChangeDirection.flat.color == DS.Text.muted)
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

    /// The web's `ui-card` (`rounded-xl`, `p-5`) and control radii.
    @Test func cardTokensFollowTheWeb() {
        #expect(DS.cardPadding == 20)
        #expect(DS.cardGroupSpacing == 12)
        #expect(DS.Radius.card == 12)
        #expect(DS.Radius.control == 8)
        #expect(DS.Radius.small == 6)
        #expect(DS.rowMinHeight == 56)
    }

    /// The web palette, exactly (tailwind.config.js and the zinc ramp).
    @Test func paletteMatchesTheWebTokens() {
        #expect(DS.Surface.page == Color(hex: 0x0F0F10))
        #expect(DS.Surface.card == Color(hex: 0x161617))
        #expect(DS.Surface.raised == Color(hex: 0x1E1E20))
        #expect(DS.Palette.accent == Color(hex: 0xEDEDED))
        #expect(DS.Palette.gain == Color(hex: 0x4CC38A))
        #expect(DS.Palette.loss == Color(hex: 0xE5484D))
        #expect(DS.Text.primary == Color(hex: 0xF4F4F5))
        #expect(DS.Text.muted == Color(hex: 0x71717A))
        // The legacy names resolve to the tokens.
        #expect(DS.Palette.success == DS.Palette.gain)
        #expect(DS.Palette.danger == DS.Palette.loss)
        #expect(DS.Palette.warning == DS.Palette.amber)
    }

    /// The sparkline never stretches less than 0.5% of the price to full height.
    @Test func sparklineDomainHasAMinimumSpan() {
        let flat = Sparkline.domain([100, 100.01, 100])
        // 0.5% of the middle value (100.005), centred on it.
        #expect(abs((flat.upperBound - flat.lowerBound) - 100.005 * 0.005) < 1e-9)
        #expect(abs((flat.upperBound + flat.lowerBound) / 2 - 100.005) < 1e-9)
        let wide = Sparkline.domain([90, 110])
        #expect(wide.lowerBound == 90 && wide.upperBound == 110)
    }

    /// Inter is bundled and registered (UIAppFonts).
    @Test func interIsRegistered() {
        for face in ["Inter-Regular", "Inter-Medium", "Inter-SemiBold"] {
            #expect(UIFont(name: face, size: 14) != nil, "\(face) is not registered")
        }
    }
}
