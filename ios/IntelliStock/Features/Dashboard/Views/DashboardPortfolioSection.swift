import Charts
import SwiftUI

/// The portfolio hero for the selected account — `_PortfolioSection` and
/// `PortfolioChart(hero: true)` in `dashboard_screen.dart` /
/// `portfolio_chart.dart`: the account switcher, the live balance, the
/// market-hours chip, the range control, the scrubbable chart, the freshness
/// line and the holdings list.
struct DashboardPortfolioSection: View {
    let accounts: [BrokerageAccount]
    let selected: BrokerageAccount
    let scope: DashboardAccountScope
    let feed: DashboardFeedModel

    @Environment(AppServices.self) private var services

    var body: some View {
        let chart = scope.chart
        VStack(alignment: .leading, spacing: 0) {
            accountSelector
                .padding(.bottom, 8)
            valueRow(chart)
            DashboardLiveStatusChip()
                .padding(.top, 8)
            Picker("Range", selection: Binding(get: { chart.range }, set: { chart.setRange($0) })) {
                ForEach(dashboardChartRanges, id: \.self) { Text($0).tag($0) }
            }
            .pickerStyle(.segmented)
            .padding(.top, 16)
            DashboardPortfolioChartArea(chart: chart)
                .padding(.top, 22)
            freshness
            DashboardHoldingsList(holdings: scope.holdings, feed: feed, brokerageId: selected.id)
        }
        // The chart keeps itself live at the range's cadence; restart on a range switch.
        .task(id: chart.range) { await chart.poll(lifecycle: services.lifecycle) }
        .task(id: scope.brokerageId) { await scope.holdings.poll(lifecycle: services.lifecycle) }
        .task(id: feed.pnlMode) { await scope.holdings.showSparks(feed.pnlMode.sparkRange) }
    }

    // MARK: Account switcher

    /// The hero's account identity: logo + upper-case label, with a chevron
    /// and a menu of every account when there is more than one.
    @ViewBuilder
    private var accountSelector: some View {
        let label = HStack(spacing: 6) {
            BrokerageLogo(brokerageType: selected.brokerageType, size: 16)
            Text(DashboardFormat.accountLabel(selected).uppercased())
                .font(.caption2.weight(.bold))
                .tracking(1.0)
                .foregroundStyle(.secondary)
            if accounts.count > 1 {
                Image(systemName: Symbol.named("expand_more"))
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
            }
        }
        if accounts.count > 1 {
            Menu {
                Picker("Account", selection: Binding(
                    get: { selected.id },
                    set: { services.selectedAccount.select($0) }
                )) {
                    ForEach(accounts) { a in
                        Label {
                            Text(DashboardFormat.accountLabel(a))
                        } icon: {
                            let type = a.brokerageType.lowercased()
                            if BrokerageLogo.assetTypes.contains(type) {
                                Image("Brand/\(type)").renderingMode(.template)
                            } else {
                                Image(systemName: BrokerageLogo.fallbackSymbol(type))
                            }
                        }
                        .tag(a.id)
                    }
                }
            } label: {
                label.contentShape(Rectangle())
            }
            .accessibilityLabel("Account, \(DashboardFormat.accountLabel(selected))")
            .accessibilityHint("Switches the account shown")
        } else {
            label
        }
    }

    // MARK: Value

    @ViewBuilder
    private func valueRow(_ chart: DashboardPortfolioChartModel) -> some View {
        if let history = chart.valueHistory {
            DashboardValueRow(history: history, scrubIndex: chart.scrubIndex)
        } else if case .failed = chart.state {
            Text(chart.state.errorMessage ?? "")
                .font(.caption)
                .foregroundStyle(DS.Palette.danger)
        } else {
            VStack(alignment: .leading, spacing: 6) {
                Skeleton(width: 200, height: 40, radius: 6)
                Skeleton(width: 120, height: 13, radius: 5)
            }
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
            .font(.caption2)
            .foregroundStyle(.tertiary)
            .padding(.top, 6)
        }
    }
}

/// The balance and its change vs the baseline (`_ValueRow`, hero style):
/// the value rolls between figures like an odometer.
struct DashboardValueRow: View {
    let history: PortfolioHistory
    let scrubIndex: Int?

    var body: some View {
        let values = history.values
        let active: Double = {
            if let scrubIndex, scrubIndex >= 0, scrubIndex < values.count { return values[scrubIndex] }
            return history.currentValue ?? values.last ?? 0
        }()
        let change = computeChange(history, scrubIndex: scrubIndex)
        let positive = (change.abs ?? 0) >= 0
        let color = positive ? DS.Palette.success : DS.Palette.danger
        VStack(alignment: .leading, spacing: 6) {
            Text(fmtMoney(active))
                .dsValueHero()
                .contentTransition(.numericText(value: active))
                .animation(.easeOut(duration: 0.5), value: active)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            HStack(spacing: 4) {
                Image(systemName: Symbol.named(positive ? "trending_up" : "trending_down"))
                    .accessibilityHidden(true)
                Text("\(fmtPnl(change.abs)) (\(fmtPct(change.pct)))")
                    .contentTransition(.numericText())
            }
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(color)
        }
        .accessibilityElement(children: .combine)
    }
}

/// "Markets Open" / "Markets Closed" — re-evaluated every 30 s so it flips
/// at the open/close boundary (`_LiveStatusChip`).
struct DashboardLiveStatusChip: View {
    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { context in
            let open = isMarketOpenAtEt(etFromUtc(context.date))
            let color = open ? DS.Palette.success : Color.secondary
            HStack(spacing: 5) {
                Circle()
                    .fill(color)
                    .frame(width: 6, height: 6)
                    .accessibilityHidden(true)
                Text(open ? "Markets Open" : "Markets Closed")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(color)
            }
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
                Skeleton(height: 224, radius: 8)
                    .padding(.horizontal, 4)
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
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .frame(height: 224)
    }
}

/// The plot itself: a monotone line over a flat area fill, a fixed
/// [0, 1440]-minute axis on 1D (the line fills only the elapsed part of the
/// day) and an index axis otherwise (gapless), a scrub hairline and dot, and
/// a pulsing dot on the latest value while not scrubbing.
private struct DashboardChartPlot: View {
    let chart: DashboardPortfolioChartModel
    let history: PortfolioHistory
    let range: String
    let animate: Bool

    @State private var selectedX: Double?
    @State private var revealed = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private static let plotHeight: CGFloat = 224

    var body: some View {
        let values = history.values
        let n = values.count
        let xs = DashboardChartGeometry.xs(history, range: range)
        let bounds = paddedBounds(values)
        let change = computeChange(history)
        let lineColor = (change.abs ?? 0) >= 0 ? DS.Palette.success : DS.Palette.danger
        let showValues = revealed || !animate || reduceMotion
        let scrub = chart.scrubIndex.flatMap { $0 < n ? $0 : nil }

        VStack(spacing: 0) {
            Chart {
                ForEach(0..<n, id: \.self) { i in
                    let v = showValues ? values[i] : bounds.min
                    AreaMark(
                        x: .value("Time", xs[i]),
                        yStart: .value("Floor", bounds.min),
                        yEnd: .value("Value", v)
                    )
                    .foregroundStyle(lineColor.opacity(DS.chartAreaOpacity))
                    .interpolationMethod(.monotone)
                    LineMark(x: .value("Time", xs[i]), y: .value("Value", v))
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
                } else if showValues, n > 0 {
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
            .frame(height: Self.plotHeight)
            .accessibilityElement()
            .accessibilityLabel("Portfolio chart")
            .accessibilityValue("\(fmtMoney(values.last)), \(fmtPct(change.pct))")

            ChartDateLabels(labels: DashboardChartGeometry.labels(history, range: range))
        }
        .padding(.horizontal, 4)
        .padding(.bottom, 8)
        .onChange(of: selectedX) { _, x in
            guard let x else {
                chart.scrubIndex = nil
                return
            }
            let index = DashboardChartGeometry.scrubIndex(selection: x, xs: xs, range: range)
            if chart.scrubIndex != index { chart.scrubIndex = index }
        }
        .sensoryFeedback(.selection, trigger: chart.scrubIndex) { _, new in new != nil }
        .onAppear {
            guard !revealed else { return }
            if animate, !reduceMotion {
                withAnimation(.easeOut(duration: 0.9)) { revealed = true }
            } else {
                revealed = true
            }
        }
    }
}

/// The scrub dot, ringed in the background colour. No glow.
private struct DashboardScrubDot: View {
    let color: Color

    var body: some View {
        Circle()
            .fill(color)
            .frame(width: 9, height: 9)
            .overlay(Circle().stroke(Color(uiColor: .systemBackground), lineWidth: 2))
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
                .overlay(Circle().stroke(Color(uiColor: .systemBackground), lineWidth: 1.5))
        }
        .frame(width: 32, height: 32)
    }
}
