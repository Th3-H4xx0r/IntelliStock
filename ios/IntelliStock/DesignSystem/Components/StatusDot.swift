import SwiftUI

/// A run state as a coloured dot plus a word, the way Home and Stocks show
/// status: "● Running", "● Stopped", "● Markets closed".
///
/// The dot carries the colour and the word stays `.secondary`, so a list of
/// rows reads calmly. Use it in an `EntityRow`'s trailing slot, a hero's status
/// line, or a `LabeledContent` value. It is not a badge: for a flag such as
/// severity, use `StatusBadge`.
///
/// `pulsing` fades the dot in and out for a live state; it holds still under
/// Reduce Motion. The dot scales with Dynamic Type.
///
///     StatusDot("Running", color: DS.Palette.success, pulsing: true)
///     StatusDot("Stopped", status: "stopped")
struct StatusDot: View {
    let label: String
    let color: Color
    var pulsing = false
    var font: Font = .subheadline

    @ScaledMetric(relativeTo: .subheadline) private var dotSize: CGFloat = 8

    init(_ label: String, color: Color, pulsing: Bool = false, font: Font = .subheadline) {
        self.label = label
        self.color = color
        self.pulsing = pulsing
        self.font = font
    }

    /// The dot coloured by a server status string (`StatusBadge.color(forStatus:)`):
    /// green for running or finished, orange for queued or paused, red for
    /// stopped or failed, secondary otherwise. `label` is the word to show.
    init(_ label: String, status: String?, pulsing: Bool = false, font: Font = .subheadline) {
        self.init(label, color: StatusBadge.color(forStatus: status), pulsing: pulsing, font: font)
    }

    var body: some View {
        HStack(spacing: 6) {
            PulsingDot(color: color, size: dotSize, pulsing: pulsing)
            Text(label)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .font(font)
        .accessibilityElement(children: .combine)
    }
}

#Preview {
    List {
        EntityRow("Alpaca Paper — forward", subtitle: "Strategy 197 · Alpaca Paper") {
            StatusDot("Running", color: DS.Palette.success, pulsing: true)
        }
        EntityRow("AI Agent Testing", subtitle: "Strategy 336") {
            StatusDot("Stopped", color: .secondary)
        }
        LabeledContent("Status") { StatusDot("Queued", status: "queued") }
    }
}
