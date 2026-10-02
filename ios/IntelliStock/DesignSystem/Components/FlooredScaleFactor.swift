import SwiftUI
import UIKit

extension View {
    /// `minimumScaleFactor` that never shrinks text below 11 pt, the HIG
    /// minimum. At the default size `.caption2` is already 11 pt, so it does
    /// not shrink at all; at larger Dynamic Type sizes it may shrink, but
    /// only down to 11 pt. `textStyle` is the style the text is set in.
    func dsMinimumScaleFactor(_ factor: CGFloat, textStyle: UIFont.TextStyle) -> some View {
        modifier(FlooredScaleFactor(factor: factor, textStyle: textStyle))
    }
}

/// The 11 pt floor for scaled-down text.
nonisolated enum DSTextFloor {
    static let minimumPointSize: CGFloat = 11

    /// The scale factor to use for text at `pointSize`: `factor`, raised so
    /// that `pointSize × factor` stays at or above 11 pt (never above 1).
    static func factor(_ factor: CGFloat, pointSize: CGFloat) -> CGFloat {
        guard pointSize > 0 else { return 1 }
        return min(1, max(factor, minimumPointSize / pointSize))
    }
}

private struct FlooredScaleFactor: ViewModifier {
    let factor: CGFloat
    let textStyle: UIFont.TextStyle

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    func body(content: Content) -> some View {
        let traits = UITraitCollection(preferredContentSizeCategory: UIContentSizeCategory(dynamicTypeSize))
        let pointSize = UIFont.preferredFont(forTextStyle: textStyle, compatibleWith: traits).pointSize
        content.minimumScaleFactor(DSTextFloor.factor(factor, pointSize: pointSize))
    }
}
