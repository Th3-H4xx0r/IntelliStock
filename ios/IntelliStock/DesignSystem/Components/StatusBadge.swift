import SwiftUI

/// The app's one badge — `StatusPill` in `status_pill.dart`: a small capsule,
/// `.caption2` semibold, the colour as text on a `DS.tintFill` (15 %) fill of
/// itself. `AppBadge` draws the same capsule.
///
/// Use at most one badge per row, and only for a state worth flagging:
/// severity, Live versus Paper, a failed run. A running or stopped state is a
/// `StatusDot`, not a badge, and who created something (user or AI) is plain
/// secondary text.
///
/// `pulsing` marks a live state with a small dot inside the capsule that fades
/// in and out, and holds still under Reduce Motion. Without it the badge is
/// text only.
///
///     StatusBadge(label: "Failed", color: DS.Palette.danger)
///     StatusBadge(label: "Live", color: DS.Palette.success, pulsing: true)
struct StatusBadge: View {
    let label: String
    let color: Color
    var pulsing = false

    var body: some View {
        HStack(spacing: 4) {
            if pulsing {
                PulsingDot(color: color, size: 5, pulsing: true)
            }
            Text(label)
        }
        .dsBadge(color)
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

extension View {
    /// The badge capsule shared by `StatusBadge` and `AppBadge`: `.caption2`
    /// semibold, `color` as text (darkened in light mode to keep 4.5:1) on a
    /// `DS.tintFill` capsule of `color`. Use it for any small tag so the app
    /// keeps one badge style.
    func dsBadge(_ color: Color) -> some View {
        modifier(DSBadgeModifier(color: color))
    }
}

private struct DSBadgeModifier: ViewModifier {
    let color: Color

    @Environment(\.colorScheme) private var colorScheme

    func body(content: Content) -> some View {
        content
            .font(.caption2.weight(.semibold))
            .foregroundStyle(DS.Palette.onTint(color, in: colorScheme))
            .lineLimit(1)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(color.opacity(DS.tintFill), in: Capsule())
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

#Preview {
    List {
        LabeledContent("Severity") { StatusBadge(label: "High", color: DS.Palette.danger) }
        LabeledContent("Mode") { StatusBadge(label: "Live", color: DS.Palette.success, pulsing: true) }
        LabeledContent("Run") { StatusBadge(label: "Queued", color: StatusBadge.color(forStatus: "queued")) }
        LabeledContent("Tag") { AppBadge(label: "real money", color: DS.Palette.danger) }
        LabeledContent("Hours") { AppBadge(label: "24/7", color: DS.Palette.info) }
    }
}
