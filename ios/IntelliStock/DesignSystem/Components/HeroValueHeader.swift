import SwiftUI

/// Which way a figure moved, and how the hero and rows colour it: gain, loss,
/// or zinc-500 when flat. (`systemImage` names an arrow for screens that
/// still want one; the web draws none.)
nonisolated enum ChangeDirection: Hashable, Sendable {
    case up, down, flat

    /// The direction of `delta`. Zero counts as up, as everywhere else in the
    /// app (`pnl >= 0` is green); nil and NaN are flat.
    init(_ delta: Double?) {
        guard let delta, !delta.isNaN else {
            self = .flat
            return
        }
        self = delta >= 0 ? .up : .down
    }

    var color: Color {
        switch self {
        case .up: DS.Palette.up
        case .down: DS.Palette.down
        case .flat: DS.Text.muted
        }
    }

    /// The SF Symbol before the change text, or nil when flat.
    var systemImage: String? {
        switch self {
        case .up: "arrow.up.right"
        case .down: "arrow.down.right"
        case .flat: nil
        }
    }

    /// What VoiceOver says before the change.
    var accessibilityPrefix: String {
        switch self {
        case .up: "Up"
        case .down: "Down"
        case .flat: ""
        }
    }
}

/// The hero at the top of a value screen, as the web dashboard and stock page
/// draw it: the big figure, the change under it, and one status line.
///
/// - **Value:** 40/500 (`DS.Font.hero`) zinc-100, monospaced digits. Pass
///   `numericValue` and the figure rolls between values with
///   `.contentTransition(.numericText(value:))`, animated by
///   `valueAnimation` (pass nil while scrubbing so it tracks the finger).
/// - **Change:** 14 pt in gain or loss, 8 pt below; `changeLabel` adds a
///   zinc-500 word after it ("Today"). No arrow.
/// - **Status:** one 12 pt zinc-500 line, such as "Market closed" or the
///   stock's name, optionally with a status dot.
///
/// Then the chart, then the range control. Never repeat the navigation
/// title here: the title names the thing, the hero shows the number.
///
///     HeroValueHeader(
///         fmtMoney(equity),
///         numericValue: equity,
///         valueAnimation: isScrubbing ? nil : .snappy,
///         change: "\(fmtPnl(change)) (\(fmtPct(pct)))",
///         direction: ChangeDirection(change),
///         status: "Markets closed",
///         statusColor: .secondary
///     )
struct HeroValueHeader<Status: View>: View {
    let value: String
    var numericValue: Double?
    var valueAnimation: Animation?
    var change: String?
    var direction: ChangeDirection
    var changeLabel: String?
    var alignment: HorizontalAlignment
    private let status: Status

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// A hero with a custom status line (a `StatusDot`, a `RelativeTimeText`),
    /// set in 12 pt zinc-500.
    init(
        _ value: String,
        numericValue: Double? = nil,
        valueAnimation: Animation? = .snappy,
        change: String? = nil,
        direction: ChangeDirection = .flat,
        changeLabel: String? = nil,
        alignment: HorizontalAlignment = .leading,
        @ViewBuilder status: () -> Status
    ) {
        self.value = value
        self.numericValue = numericValue
        self.valueAnimation = valueAnimation
        self.change = change
        self.direction = direction
        self.changeLabel = changeLabel
        self.alignment = alignment
        self.status = status()
    }

    var body: some View {
        VStack(alignment: alignment, spacing: 0) {
            Text(value)
                .dsValueHero()
                .foregroundStyle(DS.Text.primary)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
                .contentTransition(numericValue.map { ContentTransition.numericText(value: $0) } ?? .identity)
                .animation(reduceMotion ? nil : valueAnimation, value: numericValue)

            if let change {
                HStack(spacing: 6) {
                    Text(change)
                        .foregroundStyle(direction == .flat ? DS.Text.secondary : direction.color)
                    if let changeLabel {
                        Text(changeLabel)
                            .foregroundStyle(DS.Text.muted)
                    }
                }
                .font(DS.Font.body)
                .monospacedDigit()
                .lineLimit(1)
                .padding(.top, 8)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel([direction.accessibilityPrefix, change, changeLabel ?? ""]
                    .filter { !$0.isEmpty }
                    .joined(separator: " "))
            }

            status
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .padding(.top, 6)
        }
        .frame(maxWidth: .infinity, alignment: Alignment(horizontal: alignment, vertical: .center))
    }
}

/// The plain status line of a `HeroValueHeader`: text, with an optional
/// status dot before it. Draws nothing without text.
struct HeroStatusLine: View {
    let text: String?
    var dotColor: Color?

    var body: some View {
        if let text {
            if let dotColor {
                StatusDot(text, color: dotColor, font: DS.Font.caption)
            } else {
                Text(text)
            }
        }
    }
}

extension HeroValueHeader where Status == HeroStatusLine {
    /// A hero whose status line is plain text; `statusColor` adds a dot.
    init(
        _ value: String,
        numericValue: Double? = nil,
        valueAnimation: Animation? = .snappy,
        change: String? = nil,
        direction: ChangeDirection = .flat,
        changeLabel: String? = nil,
        alignment: HorizontalAlignment = .leading,
        status: String? = nil,
        statusColor: Color? = nil
    ) {
        self.init(
            value,
            numericValue: numericValue,
            valueAnimation: valueAnimation,
            change: change,
            direction: direction,
            changeLabel: changeLabel,
            alignment: alignment
        ) {
            HeroStatusLine(text: status, dotColor: statusColor)
        }
    }
}

#Preview("Hero") {
    struct Demo: View {
        @State private var equity = 5_890.05

        var body: some View {
            List {
                Section {
                    HeroValueHeader(
                        fmtMoney(equity),
                        numericValue: equity,
                        change: "+$62.13 (+1.07%)",
                        direction: .up,
                        changeLabel: "Today",
                        status: "Markets closed",
                        statusColor: .secondary
                    )
                    Button("Simulate a tick") { equity += Double.random(in: -40...40) }
                }
                Section {
                    HeroValueHeader("−$318.40", change: "−2.41%", direction: .down, status: "Backtest #175059")
                    HeroValueHeader("$0.00", status: "No trades yet")
                }
            }
        }
    }
    return Demo().dsAppearance()
}
