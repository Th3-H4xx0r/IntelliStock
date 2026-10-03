import Charts
import SwiftUI

/// Sizes the portfolio hero shares with its skeleton.
nonisolated enum DashboardPortfolioMetrics {
    static let chartHeight: CGFloat = 224
    /// The side margin of the chart, its range picker and freshness line,
    /// which sit in a full-width section of their own. A constant, never a
    /// measurement: a measured inset fed back into the layout looped forever
    /// on Back from a stock screen.
    static let chartMargin: CGFloat = 14
}

/// The portfolio for the selected account — `_PortfolioSection` and
/// `PortfolioChart(hero: true)` in `dashboard_screen.dart` /
/// `portfolio_chart.dart`, as two list sections:
///
/// 1. the hero, on the plain grouped background: the account label (which
///    opens the "Portfolios" sheet), the balance, its change, the market
///    status, the scrubbable chart, the range picker and the freshness line;
/// 2. the holdings (`DashboardHoldingsSection`).
///
/// The polls that drive them run from `DashboardView`'s list.
struct DashboardPortfolioSections: View {
    let accounts: [BrokerageAccount]
    let selected: BrokerageAccount
    let scope: DashboardAccountScope
    let feed: DashboardFeedModel
    let onSwitchAccount: () -> Void

    var body: some View {
        // The text keeps the list's own margins, in line with every section.
        Section {
            DashboardPortfolioHero(
                part: .header,
                accounts: accounts,
                selected: selected,
                chart: scope.chart,
                onSwitchAccount: onSwitchAccount
            )
            .listRowBackground(Color.clear)
            // Flush with the top of the safe area: no row padding above.
            .listRowInsets(EdgeInsets(top: 0, leading: DashboardPortfolioMetrics.chartMargin, bottom: 0, trailing: DashboardPortfolioMetrics.chartMargin))
            .listRowSeparator(.hidden)
        }
        // Full width with the chart's margins, so the balance lines up with it.
        .listSectionMargins(.horizontal, 0)
        .listSectionSpacing(0)
        // The chart runs nearly edge to edge: a full-width section, so its
        // row cannot clip it.
        Section {
            DashboardPortfolioHero(
                part: .chart,
                accounts: accounts,
                selected: selected,
                chart: scope.chart,
                onSwitchAccount: onSwitchAccount
            )
            .listRowBackground(Color.clear)
            .listRowInsets(EdgeInsets(top: 0, leading: DashboardPortfolioMetrics.chartMargin, bottom: 8, trailing: DashboardPortfolioMetrics.chartMargin))
            .listRowSeparator(.hidden)
        }
        .listSectionMargins(.horizontal, 0)
        DashboardHoldingsSection(holdings: scope.holdings, feed: feed, brokerageId: selected.id)
    }
}

/// The hero: account label, balance, change and status, with the search
/// button at their trailing end (the dashboard has no navigation bar), then
/// the chart, the range and the freshness line (`part`), as two rows: the
/// text in the list's margins, the chart nearly edge to edge.
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
            HStack(alignment: .center, spacing: 12) {
                VStack(alignment: .leading, spacing: 0) {
                    accountLabel
                        .padding(.bottom, 4)
                    value
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                DashboardSearchButton()
            }
            .padding(.top, 4)
        case .chart:
            VStack(alignment: .leading, spacing: 0) {
                DashboardPortfolioChartArea(chart: chart)
                Picker("Range", selection: Binding(get: { chart.range }, set: { chart.setRange($0) })) {
                    ForEach(dashboardChartRanges, id: \.self) { Text($0).tag($0) }
                }
                .pickerStyle(.segmented)
                .padding(.top, 12)
                freshness
            }
            .padding(.top, 20)
        }
    }

    // MARK: Account label

    /// The brokerage logo, the account's name and, with more than one
    /// account, a chevron: tapping it opens the "Portfolios" sheet.
    @ViewBuilder
    private var accountLabel: some View {
        let switchable = accounts.count > 1
        let name = DashboardFormat.accountName(selected)
        Button(action: onSwitchAccount) {
            HStack(spacing: 6) {
                BrokerageLogo(brokerageType: selected.brokerageType, size: 16)
                Text(name)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                if switchable {
                    Image(systemName: "chevron.down")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(.secondary)
                        .accessibilityHidden(true)
                }
            }
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!switchable)
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
            HeroValueHeader(
                fmtMoney(active),
                numericValue: active,
                valueAnimation: scrub == nil ? .easeOut(duration: 0.5) : nil,
                change: "\(fmtPnl(change.abs)) (\(fmtPct(change.pct)))",
                // A missing change reads green, as in Dart (`abs ?? 0 >= 0`).
                direction: ChangeDirection(change.abs ?? 0)
            ) {
                DashboardLiveStatusChip()
            }
        } else if case .failed = chart.state {
            Text(chart.state.errorMessage ?? "")
                .font(.footnote)
                .foregroundStyle(DS.Palette.danger)
        } else {
            HeroValueHeader("$0,000.00", change: "+$00.00 (+0.00%)", status: "Markets Open")
                .redacted(reason: .placeholder)
                .accessibilityLabel("Loading")
        }
    }

    // MARK: Freshness

    /// "Updated 5s ago" — ticks honestly even between polls.
    @ViewBuilder
    private var freshness: some View {
        if let updatedAt = services.dashboard.portfolioUpdatedAt {
            HStack(spacing: 0) {
                Text("Updated ")
                RelativeTimeText(timestamp: updatedAt)
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            .padding(.top, 10)
        }
    }
}

/// "Markets Open" / "Markets Closed" — re-evaluated every 30 s so it flips
/// at the open/close boundary (`_LiveStatusChip`). The hero's status line: a
/// dot and a word.
struct DashboardLiveStatusChip: View {
    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { context in
            let open = isMarketOpenAtEt(etFromUtc(context.date))
            StatusDot(
                open ? "Markets Open" : "Markets Closed",
                color: open ? DS.Palette.success : Color.secondary,
                font: .footnote
            )
        }
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
                Skeleton(height: DashboardPortfolioMetrics.chartHeight, radius: 8)
                    .padding(.bottom, 12)
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
        VStack(spacing: 8) {
            Image(systemName: Symbol.named("bar_chart_4_bars"))
                .font(.title)
                .foregroundStyle(.tertiary)
            Text(message)
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .frame(height: DashboardPortfolioMetrics.chartHeight)
    }
}

/// The plot itself: a monotone line over a flat area fill, Stocks' dotted
/// baseline at the period's opening value, a fixed [0, 1440]-minute axis on
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
        let lineColor = (change.abs ?? 0) >= 0 ? DS.Palette.success : DS.Palette.danger
        let scrub = chart.scrubIndex.flatMap { $0 < n ? $0 : nil }

        VStack(spacing: 0) {
            Chart {
                RuleMark(y: .value("Open", baseline))
                    .foregroundStyle(Color.secondary.opacity(0.5))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: DS.baselineDash))
                ForEach(0..<n, id: \.self) { i in
                    AreaMark(
                        x: .value("Time", xs[i]),
                        yStart: .value("Floor", bounds.min),
                        yEnd: .value("Value", values[i])
                    )
                    .foregroundStyle(lineColor.opacity(DS.chartAreaOpacity))
                    .interpolationMethod(.monotone)
                    LineMark(x: .value("Time", xs[i]), y: .value("Value", values[i]))
                        .foregroundStyle(lineColor)
                        .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                        .interpolationMethod(.monotone)
                }
                if let scrub {
                    RuleMark(x: .value("Time", xs[scrub]))
                        .foregroundStyle(lineColor.opacity(0.55))
                        .lineStyle(StrokeStyle(lineWidth: 1.2))
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

            ChartDateLabels(labels: DashboardChartGeometry.labels(history, range: range))
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
            .frame(width: 9, height: 9)
            .overlay(Circle().stroke(Color(uiColor: .systemGroupedBackground), lineWidth: 2))
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
                .overlay(Circle().stroke(Color(uiColor: .systemGroupedBackground), lineWidth: 1.5))
        }
        .frame(width: 32, height: 32)
    }
}
