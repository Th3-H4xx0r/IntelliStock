import SwiftUI

/// The swipe-to-send hint under an order card (the trade review and the
/// swing approval): a chevron, the action ("Sell 4.25 AAPL") in 16/500
/// zinc-100, and "Swipe up to send" in 12 pt zinc-500, brightening as the
/// card nears the threshold. The card's drag does the sending; VoiceOver and
/// Switch Control send with the default action. No colour: the action is the
/// off-white primary, as the web's confirm button.
struct SwipeUpHint: View {
    let label: String
    /// 0 at rest, 1 at the threshold.
    let progress: CGFloat
    let onSend: () -> Void

    /// Only a drag to the very top sends: the finger ends in the top tenth
    /// of the sheet, having travelled at least 40% of its height. No flick
    /// shortcut, so a drag that stops short never sends.
    nonisolated static func reachesTop(fingerY: CGFloat, travel: CGFloat, height: CGFloat) -> Bool {
        height > 0 && fingerY <= height * 0.1 && travel >= height * 0.4
    }

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(spacing: 6) {
            Image(systemName: "chevron.up")
                .font(.system(size: 20, weight: .regular))
                .foregroundStyle(DS.Text.secondary)
                .symbolEffect(.wiggle.up, options: .repeat(.periodic(delay: 1.4)), isActive: !reduceMotion && progress == 0)
            Text(progress >= 1 ? "Release to send" : label)
                .font(DS.Font.baseMedium)
                .foregroundStyle(DS.Text.primary)
                .monospacedDigit()
            Text("Swipe up to send")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .opacity(progress > 0 ? 0 : 1)
        }
        .opacity(0.75 + 0.25 * progress)
        .frame(maxWidth: .infinity, minHeight: 88)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label)
        .accessibilityAddTraits(.isButton)
        .accessibilityHint("Sends the order to the broker.")
        .accessibilityAction { onSend() }
    }
}
