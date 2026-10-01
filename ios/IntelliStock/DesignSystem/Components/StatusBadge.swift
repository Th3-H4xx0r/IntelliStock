import SwiftUI

/// A dot + label capsule for statuses — `StatusPill` in `status_pill.dart`.
/// Caption 2 semibold in the status colour on a 15 % tint of it. The dot
/// pulses for live states unless Reduce Motion is on.
struct StatusBadge: View {
    let label: String
    let color: Color
    var pulsing = false

    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        HStack(spacing: 6) {
            PulsingDot(color: color, size: 6, pulsing: pulsing)
            Text(label)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(DS.Palette.onTint(color, in: colorScheme))
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(color.opacity(DS.tintFill), in: Capsule())
        .accessibilityElement(children: .combine)
    }

    /// A sensible colour for a status string — `StatusPill.colorForStatus`.
    /// Unknown statuses read as secondary.
    static func color(forStatus status: String?) -> Color {
        switch status?.lowercased() {
        case "running", "active", "completed", "finished", "passed":
            DS.Palette.success
        case "paused", "paused_llm_critical":
            DS.Palette.warning
        case "queued", "pending":
            DS.Palette.warning
        case "building":
            DS.Palette.info
        case "stopped", "cancelled", "error", "failed", "aborted_llm_failure":
            DS.Palette.danger
        default:
            Color.secondary
        }
    }
}

/// A small status dot that fades between 40 % and full while `pulsing`, and
/// holds still under Reduce Motion.
struct PulsingDot: View {
    let color: Color
    var size: CGFloat = 6
    var pulsing = false

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let animate = pulsing && !reduceMotion
        Circle()
            .fill(color)
            .frame(width: size, height: size)
            .phaseAnimator(animate ? [1.0, 0.4] : [1.0]) { dot, opacity in
                dot.opacity(opacity)
            } animation: { _ in
                .easeInOut(duration: 1.2)
            }
            .accessibilityHidden(true)
    }
}
