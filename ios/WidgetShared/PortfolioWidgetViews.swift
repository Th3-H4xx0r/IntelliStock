// The widget's views, Apple Stocks style: the system background, the
// account name in secondary, a large value, the change in system green or red
// with an arrow, a 1D line over a light fill, and a quiet "Updated" footer.
// No gradients. The family and rendering mode are parameters (WidgetKit's
// environment values are read-only), so the unit tests render every family.

import Charts
import SwiftUI
import WidgetKit

// MARK: - Portfolio

struct PortfolioWidgetContent: View {
    let snapshot: PortfolioSnapshot?
    let family: WidgetFamily
    /// The entry date: "Updated" shows a time for today, a weekday otherwise.
    let now: Date
    /// False in the accented (tinted) and vibrant modes, where colour is
    /// replaced and only the arrow carries the direction.
    var fullColor = true

    var body: some View {
        switch family {
        case .accessoryRectangular:
            rectangular.containerBackground(.clear, for: .widget)
        case .accessoryInline:
            inline
        default:
            system
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .containerBackground(.background, for: .widget)
        }
    }

    @ViewBuilder
    private var system: some View {
        if let s = snapshot {
            Group {
                switch family {
                case .systemSmall: small(s)
                case .systemLarge, .systemExtraLarge: large(s)
                default: medium(s)
                }
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(PortfolioFormat.accessibilityLabel(name: s.name, value: s.value, changePct: s.changePct))
        } else {
            empty
        }
    }

    // MARK: Pieces

    private func trend(_ up: Bool) -> Color {
        fullColor ? (up ? .green : .red) : .primary
    }

    private func name(_ s: PortfolioSnapshot) -> some View {
        Text(PortfolioFormat.displayName(s.name))
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(.secondary)
            .lineLimit(1)
    }

    private func value(_ s: PortfolioSnapshot, font: Font) -> some View {
        Text(PortfolioFormat.money(s.value))
            .font(font.weight(.semibold))
            .monospacedDigit()
            .foregroundStyle(.primary)
            .lineLimit(1)
            .minimumScaleFactor(0.5)
            .contentTransition(.numericText(value: s.value))
            .widgetAccentable()
    }

    private func change(_ s: PortfolioSnapshot, _ text: String, font: Font) -> some View {
        HStack(spacing: 3) {
            Image(systemName: PortfolioFormat.arrow(up: s.isUp))
                .imageScale(.small)
            Text(text)
                .monospacedDigit()
                .contentTransition(.numericText(value: s.changeAbs))
        }
        .font(font.weight(.semibold))
        .foregroundStyle(trend(s.isUp))
        .lineLimit(1)
        .minimumScaleFactor(0.7)
        .widgetAccentable()
    }

    @ViewBuilder
    private func updated(_ s: PortfolioSnapshot) -> some View {
        if let synced = s.syncedAt {
            Text(PortfolioFormat.updated(min(synced, now), now: now))
                .font(.caption2)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
    }

    private func chart(_ s: PortfolioSnapshot, lineWidth: CGFloat) -> some View {
        PortfolioSparkline(points: s.points, color: trend(s.isUp), fullColor: fullColor, lineWidth: lineWidth)
    }

    private func holdingRow(_ h: HoldingItem, showValue: Bool) -> some View {
        HStack(spacing: 8) {
            Text(h.symbol)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.primary)
            Spacer(minLength: 4)
            if showValue {
                Text(PortfolioFormat.money(h.marketValue))
                    .font(.subheadline)
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }
            Text(PortfolioFormat.signedPercent(h.pnlPct))
                .font(.subheadline.weight(.medium))
                .monospacedDigit()
                .foregroundStyle(trend(h.pnlPct >= 0))
                .frame(minWidth: showValue ? 70 : 0, alignment: .trailing)
        }
        .lineLimit(1)
        .minimumScaleFactor(0.8)
    }

    // MARK: Families

    private func small(_ s: PortfolioSnapshot) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            name(s)
            value(s, font: .title)
            change(s, PortfolioFormat.signedPercent(s.changePct), font: .caption)
            Spacer(minLength: 6)
            chart(s, lineWidth: 1.75)
                .frame(maxHeight: 40)
            updated(s)
                .padding(.top, 4)
        }
    }

    private func medium(_ s: PortfolioSnapshot) -> some View {
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 2) {
                name(s)
                value(s, font: .title)
                change(s, PortfolioFormat.change(abs: s.changeAbs, pct: s.changePct), font: .subheadline)
                Spacer(minLength: 8)
                chart(s, lineWidth: 2)
                    .frame(maxHeight: 44)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)

            VStack(alignment: .trailing, spacing: 7) {
                if s.holdings.isEmpty {
                    Text("No positions")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(Array(s.holdings.prefix(3).enumerated()), id: \.offset) { _, h in
                        holdingRow(h, showValue: false)
                    }
                }
                Spacer(minLength: 4)
                updated(s)
            }
            .frame(width: 118)
            .frame(maxHeight: .infinity)
        }
    }

    private func large(_ s: PortfolioSnapshot) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            name(s)
            value(s, font: .largeTitle)
            change(s, PortfolioFormat.change(abs: s.changeAbs, pct: s.changePct), font: .subheadline)
            chart(s, lineWidth: 2)
                .frame(minHeight: 60, maxHeight: .infinity)
                .padding(.top, 10)
            Divider()
                .padding(.vertical, 10)
            if s.holdings.isEmpty {
                Text("No positions")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            } else {
                VStack(spacing: 7) {
                    ForEach(Array(s.holdings.prefix(5).enumerated()), id: \.offset) { _, h in
                        holdingRow(h, showValue: true)
                    }
                }
            }
            updated(s)
                .frame(maxWidth: .infinity, alignment: .trailing)
                .padding(.top, 8)
        }
    }

    private var empty: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("IntelliStock")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.secondary)
                .widgetAccentable()
            Spacer(minLength: 0)
            Text("Open the app to sync your portfolio.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: Lock Screen

    @ViewBuilder
    private var rectangular: some View {
        if let s = snapshot {
            VStack(alignment: .leading, spacing: 0) {
                Text(PortfolioFormat.displayName(s.name))
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                Text(PortfolioFormat.money(s.value))
                    .font(.headline)
                    .monospacedDigit()
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                    .widgetAccentable()
                HStack(spacing: 2) {
                    Image(systemName: PortfolioFormat.arrow(up: s.isUp))
                    Text(PortfolioFormat.change(abs: s.changeAbs, pct: s.changePct))
                        .monospacedDigit()
                }
                .font(.caption)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(PortfolioFormat.accessibilityLabel(name: s.name, value: s.value, changePct: s.changePct))
        } else {
            VStack(alignment: .leading, spacing: 0) {
                Text("IntelliStock").font(.caption.weight(.semibold))
                Text("Open the app to sync").font(.caption2).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    @ViewBuilder
    private var inline: some View {
        if let s = snapshot {
            Label {
                Text("\(PortfolioFormat.money(s.value)) \(PortfolioFormat.signedPercent(s.changePct))")
            } icon: {
                Image(systemName: PortfolioFormat.arrow(up: s.isUp))
            }
        } else {
            Text("IntelliStock")
        }
    }
}

/// The 1D line over a light flat fill. Hidden from VoiceOver: the widget's
/// combined label already says which way the day went.
struct PortfolioSparkline: View {
    let points: [SeriesPoint]
    let color: Color
    var fullColor = true
    var lineWidth: CGFloat = 2

    var body: some View {
        if points.count >= 2, let y = PortfolioSeries.domain(for: points),
           let t0 = points.first?.t, let t1 = points.last?.t, t1 > t0 {
            Chart {
                ForEach(Array(points.enumerated()), id: \.offset) { _, p in
                    AreaMark(x: .value("Time", p.t),
                             yStart: .value("Floor", y.lowerBound),
                             yEnd: .value("Value", p.v))
                        .foregroundStyle(color.opacity(fullColor ? 0.14 : 0.25))
                    LineMark(x: .value("Time", p.t), y: .value("Value", p.v))
                        .foregroundStyle(color)
                        .lineStyle(StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round))
                }
            }
            .chartXScale(domain: t0...t1)
            .chartYScale(domain: y)
            .chartXAxis(.hidden)
            .chartYAxis(.hidden)
            .chartLegend(.hidden)
            .widgetAccentable()
            .accessibilityHidden(true)
        } else {
            Color.clear
        }
    }
}

// MARK: - Instances

struct InstanceStatusContent: View {
    let items: [InstanceItem]
    let family: WidgetFamily
    var fullColor = true

    private var limit: Int { family == .systemLarge ? 9 : 4 }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text("Instances")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.secondary)
                Spacer(minLength: 4)
                if !items.isEmpty {
                    Text("\(items.filter(\.running).count) running")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .widgetAccentable()
                }
            }
            if items.isEmpty {
                Spacer(minLength: 0)
                Text("Open the app to sync your instances.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(Array(items.prefix(limit).enumerated()), id: \.offset) { _, item in
                    row(item)
                }
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .containerBackground(.background, for: .widget)
    }

    private func row(_ item: InstanceItem) -> some View {
        HStack(spacing: 8) {
            Circle()
                .fill(item.running ? (fullColor ? Color.green : Color.primary) : Color.secondary.opacity(0.5))
                .frame(width: 7, height: 7)
                .widgetAccentable(item.running)
            Text(PortfolioFormat.displayName(item.name))
                .font(.subheadline)
                .foregroundStyle(item.running ? .primary : .secondary)
                .lineLimit(1)
            if family != .systemSmall, item.pnlPct != 0 {
                Spacer(minLength: 6)
                Text(PortfolioFormat.signedPercent(item.pnlPct))
                    .font(.subheadline)
                    .monospacedDigit()
                    .foregroundStyle(fullColor ? (item.pnlPct >= 0 ? Color.green : Color.red) : Color.primary)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(PortfolioFormat.displayName(item.name)), \(item.running ? "running" : "stopped")")
    }
}
