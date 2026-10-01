import SwiftUI
import UIKit

/// A brokerage's brand mark as a single-colour glyph — `BrokerageLogo` in
/// `brokerage_logo.dart`. The asset-catalog SVG `Brand/<type>` renders as a
/// template tinted with `color` (the accent by default), so every brokerage
/// reads as one family. A type with no asset falls back to the SF Symbol the
/// app used before the logos existed. Decorative: the row's label names the
/// brokerage for VoiceOver.
struct BrokerageLogo: View {
    let brokerageType: String
    var size: CGFloat = 16
    var color: Color?

    /// Asset names per `brokerage_type` (the API sends them lower-case).
    static let assetTypes: Set<String> = ["alpaca", "kalshi", "binanceus"]

    /// The pre-logo symbol, still used when a brand asset is absent.
    static func fallbackSymbol(_ brokerageType: String) -> String {
        brokerageType.lowercased() == "alpaca" ? Symbol.named("show_chart") : Symbol.named("savings")
    }

    var body: some View {
        let type = brokerageType.lowercased()
        let tint = color ?? DS.Palette.accent
        Group {
            if Self.assetTypes.contains(type), UIImage(named: "Brand/\(type)") != nil {
                Image("Brand/\(type)")
                    .renderingMode(.template)
                    .resizable()
                    .scaledToFit()
            } else {
                Image(systemName: Self.fallbackSymbol(type))
                    .resizable()
                    .scaledToFit()
            }
        }
        .foregroundStyle(tint)
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}
