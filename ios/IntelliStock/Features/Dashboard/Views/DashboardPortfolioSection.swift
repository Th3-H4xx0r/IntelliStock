import Charts
import SwiftUI

/// Sizes the portfolio hero shares with its skeleton.
nonisolated enum DashboardPortfolioMetrics {
    /// The web hero's chart is 300 px on a wider column; 280 on a phone.
    static let chartHeight: CGFloat = 280
    /// The side margin of the hero, its chart and its range row: the page
    /// gutter. A constant, never a measurement: a measured inset fed back
    /// into the layout looped forever on Back from a stock screen.
    static let chartMargin: CGFloat = DS.pageGutter
}

/// The web's caption after a change figure: "Today", "Past week", …
nonisolated func dashboardRangeCaption(_ range: String) -> String {
    switch range {
    case "1D": "Today"
    case "1W": "Past week"
    case "1M": "Past month"
    case "3M": "Past 3 months"
    case "YTD": "Year to date"
    case "1Y": "Past year"
    case "ALL": "All time"
    default: ""
    }
}

/// A scrubbed point's caption: "Nov 12, 3:41 PM" on 1D and 1W, else "Nov 12, 2026".
nonisolated func dashboardScrubCaption(_ date: Date, range: String) -> String {
    let withTime = range == "1D" || range == "1W"
    return withTime
        ? date.formatted(.dateTime.month(.abbreviated).day().hour().minute())
        : date.formatted(.dateTime.month(.abbreviated).day().year())
}

/// The portfolio for the selected account — the web's hero
/// (`PortfolioChart variant="hero"`), drawn straight on the page:
///
/// 1. the account's name and kind, the value, its change and the range's
///    caption ("Today"), the scrubbable chart, the range row and the
///    freshness line;
/// 2. the holdings (`DashboardHoldingsSection`);
/// 3. for a stock account, its orders (`DashboardOrdersSection`).
///
/// The polls that drive them run from `DashboardView`'s list.
struct DashboardPortfolioSections: View {
    let accounts: [BrokerageAccount]
    let selected: BrokerageAccount
    let scope: DashboardAccountScope
    let feed: DashboardFeedModel
    let onSwitchAccount: () -> Void

    var body: some View {
        Section {
            DashboardPortfolioHero(
                part: .header,
                accounts: accounts,
                selected: selected,
                chart: scope.chart,
                onSwitchAccount: onSwitchAccount
            )
            // The list's 32 pt section gap is the web's `mt-8` under the header.
            .dsClearRow(top: 0)
            DashboardPortfolioHero(
                part: .chart,
                accounts: accounts,
                selected: selected,
                chart: scope.chart,
                onSwitchAccount: onSwitchAccount
            )
            .dsClearRow(top: 16)
        }
        DashboardHoldingsSection(holdings: scope.holdings, feed: feed, brokerageId: selected.id)
        if selected.hasOrderBook {
            DashboardOrdersSection(orders: scope.orders)
        }
    }
}

/// The hero: the account's name and kind, the value and its change, then
/// the chart, the range row and the freshness line (`part`), as two rows.
private struct DashboardPortfolioHero: View {
    enum Part { case header, chart }

    let part: Part
    let accounts: [BrokerageAccount]
    let selected: BrokerageAccount
    let chart: DashboardPortfolioChartModel
    let onSwitchAccount: () -> Void

    @Environment(AppServices.self) private var services

    var body: some View {
        switch part {
        case .header:
            VStack(alignment: .leading, spacing: 0) {
                accountLabel
                value
                    .padding(.top, 16)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        case .chart:
            VStack(alignment: .leading, spacing: 0) {
                DashboardPortfolioChartArea(chart: chart)
                DashboardRangeBar(
                    selection: Binding(get: { chart.range }, set: { chart.setRange($0) }),
                    activeColor: heroColor
                )
                .padding(.top, 16)
                freshness
                DSDivider()
                    .padding(.top, 16)
            }
        }
    }

    /// The line colour: gain when the range ends at or above its start.
    private var heroColor: Color {
        guard let history = chart.valueHistory else { return DS.Text.soft }
        return (computeChange(history).abs ?? 0) >= 0 ? DS.Palette.gain : DS.Palette.loss
    }

    // MARK: Account label

    /// The account's name (14/500 zinc-200) over its kind ("Alpaca · Live").
    /// Tapping it opens the Portfolios sheet when there is more than one
    /// account, as the header's switcher does.
    @ViewBuilder
    private var accountLabel: some View {
        let switchable = accounts.count > 1
        let name = DashboardFormat.accountName(selected)
        Button(action: onSwitchAccount) {
            VStack(alignment: .leading, spacing: 4) {
                Text(name)
                    .font(DS.Font.bodyMedium)
                    .foregroundStyle(DS.Text.soft)
                    .lineLimit(1)
                Text(dashboardAccountKind(selected))
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!switchable)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Account, \(name)")
        .accessibilityHint(switchable ? "Shows your portfolios" : "")
    }

    // MARK: Value

    @ViewBuilder
    private var value: some View {
        if let history = chart.valueHistory {
            let scrub = chart.scrubIndex
            let active = dashboardHeroValue(history, scrubIndex: scrub)
            let change = computeChange(history, scrubIndex: scrub)
            let caption = scrub.flatMap { i in i < history.timestamps.count ? dashboardScrubCaption(history.timestamps[i], range: chart.range) : nil }
                ?? dashboardRangeCaption(chart.range)
            HeroValueHeader(
                fmtMoney(active),
                numericValue: active,
                valueAnimation: scrub == nil ? DSMotion.ease(0.5) : nil,
                change: "\(fmtPnl(change.abs)) (\(fmtPct(change.pct)))",
                // A missing change reads green, as in Dart (`abs ?? 0 >= 0`).
                direction: ChangeDirection(change.abs ?? 0),
                changeLabel: caption
            )
        } else if case .failed = chart.state {
            Text(chart.state.errorMessage ?? "")
                .font(DS.Font.body)
                .foregroundStyle(DS.Palette.loss)
        } else {
            VStack(alignment: .leading, spacing: 12) {
                Skeleton(width: 256, height: 44, radius: DS.Radius.small)
                Skeleton(width: 160, height: 16)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Loading")
        }
    }

    // MARK: Freshness

    /// "Updated 5s ago" — ticks honestly even between polls.
    @ViewBuilder
    private var freshness: some View {
        if let updatedAt = services.dashboard.portfolioUpdatedAt {
            // "Updated just now", "Updated 5m ago": sentence case after "Updated".
            TimelineView(.periodic(from: .now, by: 20)) { context in
                let label = RelativeTimeText.label(for: updatedAt, now: context.date)
                Text("Updated \(label.prefix(1).lowercased() + label.dropFirst())")
            }
            .font(DS.Font.caption)
            .foregroundStyle(DS.Text.muted)
            .padding(.top, 12)
        }
    }
}

/// The page header's subtitle: today's date, then "Market open" (a live
/// dot) or "Market closed" (a grey dot), re-evaluated every 30 s so it flips
/// at the open/close boundary (`_LiveStatusChip`).
struct DashboardLiveStatusChip: View {
    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { context in
            let open = isMarketOpenAtEt(etFromUtc(context.date))
            HStack(spacing: 8) {
                Text(context.date.formatted(.dateTime.weekday(.wide).month(.wide).day()))
                Text("·").foregroundStyle(DS.Text.faint).accessibilityHidden(true)
                HStack(spacing: 6) {
                    DSStatusDot(open ? .live : .off)
                    Text(open ? "Market open" : "Market closed")
                        .foregroundStyle(open ? DS.Text.label : DS.Text.muted)
                }
            }
            .font(DS.Font.pageSub)
            .foregroundStyle(DS.Text.muted)
            .lineLimit(1)
            .minimumScaleFactor(0.85)
            .accessibilityElement(children: .combine)
        }
    }
}

/// The hero's range row (the web's): 32 pt buttons in 12/500, the active one
/// on a white-6% fill in the line's colour, the rest zinc-500.
struct DashboardRangeBar: View {
    @Binding var selection: String
    var ranges: [String] = dashboardChartRanges
    var activeColor: Color

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: 4) {
            ForEach(ranges, id: \.self) { range in
                let active = range == selection
                Button {
                    withAnimation(DSMotion.standard(reduceMotion: reduceMotion)) { selection = range }
                } label: {
                    Text(range)
                        .font(DS.Font.captionMedium)
                        .monospacedDigit()
                        .foregroundStyle(active ? activeColor : DS.Text.muted)
                        .padding(.horizontal, 12)
                        .frame(height: 32)
                        .background(active ? Color.white.opacity(0.06) : Color.clear, in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(active ? .isSelected : [])
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Range")
    }
}

/// The scrubbable equity chart (`_ChartArea` and its loading/empty states).
struct DashboardPortfolioChartArea: View {
    let chart: DashboardPortfolioChartModel

    var body: some View {
        switch chart.state {
        case .loading:
            // The skeleton is a first-load affordance only; a range switch
            // keeps the outgoing curve on screen until the new data lands.
            if let held = chart.lastHistory, !held.isEmpty {
                DashboardChartPlot(chart: chart, history: held, range: chart.lastLoadedRange, animate: false)
                    .id(chart.lastLoadedRange)
            } else {
                Skeleton(height: DashboardPortfolioMetrics.chartHeight, radius: DS.Radius.control)
            }
        case .failed:
            DashboardChartEmpty(message: "Failed to load")
        case .loaded(let history):
            if history.isEmpty {
                DashboardChartEmpty(message: "No data for this range")
            } else {
                // A new range remounts the plot, which draws itself in;
                // polls keep the same identity and stay still.
                DashboardChartPlot(chart: chart, history: history, range: chart.range, animate: true)
                    .id(chart.range)
            }
        }
    }
}

private struct DashboardChartEmpty: View {
    let message: String

    var body: some View {
        Text(message)
            .font(DS.Font.body)
            .foregroundStyle(DS.Text.muted)
            .frame(maxWidth: .infinity)
            .frame(height: DashboardPortfolioMetrics.chartHeight)
    }
}

/// The plot itself: a 2 pt line over a faint same-hue fade (the web hero's),
/// the dotted opening-value line, a fixed [0, 1440]-minute axis on
/// 1D (the line fills only the elapsed part of the day) and an index axis
/// otherwise (gapless), a scrub hairline and dot, and a pulsing dot on the
/// latest value while not scrubbing. It draws itself in from the leading edge
/// for each account and range (`chartDrawIn`); polls leave it still.
private struct DashboardChartPlot: View {
    let chart: DashboardPortfolioChartModel
    let history: PortfolioHistory
    let range: String
    let animate: Bool

    @State private var selectedX: Double?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let values = history.values
        let n = values.count
        let xs = DashboardChartGeometry.xs(history, range: range)
        let baseline = history.openValue ?? values[0]
        let bounds = paddedBounds(values + [baseline])
        let change = computeChange(history)
        let lineColor = (change.abs ?? 0) >= 0 ? DS.Palette.gain : DS.Palette.loss
        let scrub = chart.scrubIndex.flatMap { $0 < n ? $0 : nil }

        VStack(spacing: 0) {
            Chart {
                RuleMark(y: .value("Open", baseline))
                    .foregroundStyle(DS.Chart.baseline)
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [2, 2]))
                ForEach(0..<n, id: \.self) { i in
                    AreaMark(
                        x: .value("Time", xs[i]),
                        yStart: .value("Floor", bounds.min),
                        yEnd: .value("Value", values[i])
                    )
                    // The faint same-hue fade under a P&L line (web hero).
                    .foregroundStyle(LinearGradient(
                        colors: [lineColor.opacity(DS.Chart.areaTopOpacity), lineColor.opacity(0)],
                        startPoint: .top,
                        endPoint: .bottom
                    ))
                    .interpolationMethod(.monotone)
                    LineMark(x: .value("Time", xs[i]), y: .value("Value", values[i]))
                        .foregroundStyle(lineColor)
                        .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                        .interpolationMethod(.monotone)
                }
                if let scrub {
                    RuleMark(x: .value("Time", xs[scrub]))
                        .foregroundStyle(Color.white.opacity(0.3))
                        .lineStyle(StrokeStyle(lineWidth: 1))
                    PointMark(x: .value("Time", xs[scrub]), y: .value("Value", values[scrub]))
                        .symbol { DashboardScrubDot(color: lineColor) }
                } else if n > 0 {
                    PointMark(x: .value("Time", xs[n - 1]), y: .value("Value", values[n - 1]))
                        .symbol { DashboardEndDot(color: lineColor, pulsing: !reduceMotion) }
                }
            }
            .chartXScale(domain: DashboardChartGeometry.domain(range: range, count: n))
            .chartYScale(domain: bounds.min...bounds.max)
            .chartXAxis(.hidden)
            .chartYAxis(.hidden)
            .chartLegend(.hidden)
            .chartXSelection(value: $selectedX)
            .frame(height: DashboardPortfolioMetrics.chartHeight)
            .chartDrawIn(
                trigger: DashboardChartGeometry.drawInKey(accountId: chart.accountId, range: range),
                enabled: animate,
                interacting: selectedX != nil
            )
            .accessibilityElement()
            .accessibilityLabel("Portfolio chart")
            .accessibilityValue("\(fmtMoney(values.last)), \(fmtPct(change.pct))")
            // No axis labels: the web hero draws the line alone.
        }
        .onChange(of: selectedX) { _, x in
            guard let x else {
                chart.scrubIndex = nil
                return
            }
            let index = DashboardChartGeometry.scrubIndex(selection: x, xs: xs, range: range)
            if chart.scrubIndex != index { chart.scrubIndex = index }
        }
        .sensoryFeedback(.selection, trigger: chart.scrubIndex) { _, new in new != nil }
    }
}

/// The scrub dot, ringed in the background colour. No glow.
private struct DashboardScrubDot: View {
    let color: Color

    var body: some View {
        Circle()
            .fill(color)
            .frame(width: 7, height: 7)
            .overlay(Circle().stroke(DS.Surface.page, lineWidth: 2))
    }
}

/// The latest-value marker: a solid dot with an outline ring that expands
/// and fades — the live "ping" (`_PulsingEndDot`), drawn without a glow.
private struct DashboardEndDot: View {
    let color: Color
    let pulsing: Bool

    var body: some View {
        ZStack {
            if pulsing {
                Circle()
                    .stroke(color, lineWidth: 1.5)
                    .frame(width: 8, height: 8)
                    .phaseAnimator([0.0, 1.0]) { ring, t in
                        ring.scaleEffect(1 + t * 3).opacity(1 - t)
                    } animation: { t in
                        t == 1 ? .easeOut(duration: 1.6) : .linear(duration: 0)
                    }
            }
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)
                .overlay(Circle().stroke(DS.Surface.page, lineWidth: 1.5))
        }
        .frame(width: 32, height: 32)
    }
}
