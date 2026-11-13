import SwiftUI

/// What a status dot says (`ui-dot-live|warn|off|error`), and the colour of
/// the word beside it.
///
/// - `live`: running or live — a gain dot with a soft ring. The only green
///   status, and the only one that may ping.
/// - `positive`: done and good (a check passed) — a plain gain dot.
/// - `neutral`: done (completed, finished, filled) — a zinc-400 dot.
/// - `warn`: paused, queued for a person, pending cancel — an amber dot.
/// - `off`: stopped, cancelled, queued, idle — a zinc-600 dot.
/// - `error`: failed, rejected — a loss dot.
nonisolated enum DSStatusTone: Hashable, Sendable {
    case live, positive, neutral, warn, off, error

    /// The web's map from a server status (backtests, instances, engines).
    init(status: String?) {
        switch status?.lowercased() {
        case "running", "active", "live", "open": self = .live
        case "passed", "success", "succeeded": self = .positive
        case "completed", "finished", "filled", "done", "building": self = .neutral
        case "paused", "paused_llm_critical", "pending_cancel", "warning": self = .warn
        case "error", "failed", "aborted_llm_failure", "rejected", "crashed": self = .error
        default: self = .off
        }
    }

    /// The tone a legacy colour argument means (`StatusDot(color:)`).
    init(color: Color) {
        switch color {
        case DS.Palette.gain: self = .live
        case DS.Palette.amber: self = .warn
        case DS.Palette.loss: self = .error
        case DS.Text.secondary, DS.Text.soft, DS.Text.primary, DS.Text.label, DS.Palette.accent: self = .neutral
        default: self = .off
        }
    }

    var dotColor: Color {
        switch self {
        case .live, .positive: DS.Palette.gain
        case .neutral: DS.Text.secondary
        case .warn: DS.Palette.amber
        case .off: DS.Text.faint
        case .error: DS.Palette.loss
        }
    }

    /// The word's colour: brighter for what is happening now.
    var wordColor: Color {
        switch self {
        case .live, .positive: DS.Text.soft
        case .neutral, .warn, .error: DS.Text.label
        case .off: DS.Text.muted
        }
    }
}

/// The status dot (`ui-dot`): 6 pt. A live one wears a soft 3 pt ring and,
/// with `pinging`, a ring that expands and fades every second (held still
/// under Reduce Motion) — keep the ping for things genuinely live.
struct DSStatusDot: View {
    var tone: DSStatusTone
    var pinging = false
    @ScaledMetric(relativeTo: .caption) private var size: CGFloat = 6

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    init(_ tone: DSStatusTone, pinging: Bool = false) {
        self.tone = tone
        self.pinging = pinging
    }

    var body: some View {
        ZStack {
            if tone == .live {
                Circle()
                    .fill(DS.Palette.gain.opacity(0.15))
                    .frame(width: size + 6, height: size + 6)
                if pinging && !reduceMotion {
                    Circle()
                        .fill(DS.Palette.gain)
                        .frame(width: size, height: size)
                        .phaseAnimator([0.0, 1.0]) { ring, t in
                            ring.scaleEffect(1 + t * 1.6).opacity(0.75 * (1 - t))
                        } animation: { t in
                            t == 1 ? .timingCurve(0, 0, 0.2, 1, duration: 1.0) : .linear(duration: 0)
                        }
                }
            }
            Circle()
                .fill(tone.dotColor)
                .frame(width: size, height: size)
        }
        .frame(width: size + 6, height: size + 6)
        .accessibilityHidden(true)
    }
}

/// The live indicator alone: a gain dot with its soft ring and a ping.
struct DSLiveDot: View {
    var pinging = true

    var body: some View {
        DSStatusDot(.live, pinging: pinging)
    }
}

/// A status as a dot and a word — "● Running", "● Stopped", "● Market
/// closed" — never a coloured pill. The word is 14 pt (or `font`) in the
/// tone's colour: zinc-200 for live, zinc-300 for paused or failed, zinc-500
/// for stopped.
///
///     StatusDot("Running", tone: .live, pulsing: true)
///     StatusDot("Stopped", status: "stopped")
///     StatusDot("Market closed", color: .secondary, font: DS.Font.caption)
struct StatusDot: View {
    let label: String
    let tone: DSStatusTone
    var pulsing = false
    var font: Font = DS.Font.body

    init(_ label: String, tone: DSStatusTone, pulsing: Bool = false, font: Font = DS.Font.body) {
        self.label = label
        self.tone = tone
        self.pulsing = pulsing
        self.font = font
    }

    /// The dot for a legacy colour: gain → live, amber → paused, loss →
    /// error, zinc-400 → done, anything else → stopped.
    init(_ label: String, color: Color, pulsing: Bool = false, font: Font = DS.Font.body) {
        self.init(label, tone: DSStatusTone(color: color), pulsing: pulsing, font: font)
    }

    /// The dot for a server status string (`DSStatusTone(status:)`).
    init(_ label: String, status: String?, pulsing: Bool = false, font: Font = DS.Font.body) {
        self.init(label, tone: DSStatusTone(status: status), pulsing: pulsing, font: font)
    }

    var body: some View {
        HStack(spacing: 6) {
            DSStatusDot(tone, pinging: pulsing && tone == .live)
            Text(label)
                .font(font)
                .foregroundStyle(tone.wordColor)
                .lineLimit(1)
        }
        .accessibilityElement(children: .combine)
    }
}

/// A flag on a row (severity, Live versus Paper, a failed run). It used to
/// be a tinted capsule; the web has no pills, so it is now a dot and a
/// 12 pt word in the tone of `color` (`DSStatusTone(color:)`).
///
///     StatusBadge(label: "Failed", color: DS.Palette.loss)
///     StatusBadge(label: "Live", color: DS.Palette.gain, pulsing: true)
struct StatusBadge: View {
    let label: String
    let color: Color
    var pulsing = false

    var body: some View {
        StatusDot(label, tone: DSStatusTone(color: color), pulsing: pulsing, font: DS.Font.caption)
    }

    /// The dot colour for a server status — the web's map: running green,
    /// done zinc-400, paused amber, stopped or queued zinc-600, failed red.
    static func color(forStatus status: String?) -> Color {
        DSStatusTone(status: status).dotColor
    }
}

extension View {
    /// A small text flag (`AppBadge`, a side or a kind beside a title): the
    /// web shows these as quiet 12 pt text, so this draws no capsule. Gain
    /// and loss keep their colour (a Buy, a Sell); amber stays amber (a
    /// warning word); every other colour reads zinc-400.
    func dsBadge(_ color: Color) -> some View {
        let ink: Color = switch color {
        case DS.Palette.gain, DS.Palette.loss, DS.Palette.amber: color
        default: DS.Text.secondary
        }
        return self
            .font(DS.Font.caption)
            .foregroundStyle(ink)
            .lineLimit(1)
    }
}

/// A status dot that fades between 40% and full while `pulsing` (held still
/// under Reduce Motion). Kept for existing call sites; new code uses
/// `DSStatusDot`.
struct PulsingDot: View {
    let color: Color
    var size: CGFloat = 6
    var pulsing = false

    var body: some View {
        DSStatusDot(DSStatusTone(color: color), pinging: pulsing)
    }
}

#Preview {
    List {
        DSSection("Statuses") {
            StatusDot("Running", tone: .live, pulsing: true)
            StatusDot("Paused", status: "paused")
            StatusDot("Completed", status: "completed")
            StatusDot("Stopped", status: "stopped")
            StatusDot("Failed", status: "failed")
            LabeledContent("Mode") { StatusBadge(label: "Live money", color: DS.Palette.amber) }
            LabeledContent("Tag") { AppBadge(label: "real money", color: DS.Palette.loss) }
        }
    }
    .dsList()
    .dsAppearance()
}
