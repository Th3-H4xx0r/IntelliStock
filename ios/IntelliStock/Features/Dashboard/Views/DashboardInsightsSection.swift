import SwiftUI

/// The "Insights" and "Market" groups beneath the portfolio —
/// `InsightsSection` in `insights_section.dart` — as list sections:
///
/// - **Insights:** today's movers and a horizontally scrolling row of
///   compact cards (Today, Diversification, Risk), then the sector
///   allocation as the 3D drill-in ring (`Sector3DChart`) in its own card,
///   kept out of the scrolling row so its swipes never compete with it;
/// - **Market:** the index cards, sector performance, the account's market
///   movers, nexus momentum and market news.
///
/// Cards that load empty hide themselves. The loads run from
/// `DashboardView`'s list; a news tap hands its link back through `openLink`.
struct DashboardInsightsSections: View {
    let scope: DashboardAccountScope
    let feed: DashboardFeedModel
    let openLink: (DashboardBrowserLink) -> Void

    @Environment(AppServices.self) private var services

    private var id: String { scope.brokerageId }

    var body: some View {
        let insights = scope.insights
        insightsSection(insights)
        sectorAllocationSection
        marketSection
        sectorPerformanceSection
        DashboardMarketMoversSection(data: insights.marketMovers)
        momentumSection(insights.momentum)
        newsSection
    }

    private func stockRoute(_ symbol: String, brokerageId: String?) -> Route {
        .stock(StockRoute(symbol: symbol, brokerageId: brokerageId))
    }

    /// Tiles inside a scrolling row are buttons, not `NavigationLink`s: a
    /// link nested in a row would turn the whole row into one link.
    private func openStock(_ symbol: String, brokerageId: String?) {
        services.router.push(stockRoute(symbol, brokerageId: brokerageId))
    }

    // MARK: Insights: movers and compact cards

    private func insightsSection(_ insights: DashboardAccountInsightsModel) -> some View {
        Section {
            if let movers = insights.todaysMovers, !movers.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(movers, id: \.symbol) { m in
                            Button { openStock(m.symbol, brokerageId: id) } label: {
                                DashboardMoverTile(mover: m)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
                .scrollClipDisabled()
                .dashboardClearRow()
            }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(alignment: .top, spacing: 12) {
                    dayPnlCard
                    diversificationCard(insights.concentration)
                    riskCard
                }
                .fixedSize(horizontal: false, vertical: true)
                .scrollTargetLayout()
            }
            // Each swipe settles on a card, as App Store shelves do.
            .scrollTargetBehavior(.viewAligned)
            .scrollClipDisabled()
            .dashboardClearRow()
        } header: {
            DashboardGroupHeader(group: "Insights")
        }
    }

    private var dayPnlCard: some View {
        let loaded = feed.dayChangeLoaded(id)
        let d = feed.dayChange[id] ?? nil
        return DashboardInsightCard("Today") {
            if !loaded {
                StatGrid {
                    StatCell(label: "Day P&L", value: "+$00.00")
                    StatCell(label: "Change", value: "+0.00%")
                }
                .redacted(reason: .placeholder)
            } else if let d {
                let direction = ChangeDirection(d.abs)
                StatGrid {
                    StatCell(label: "Day P&L") {
                        Text(fmtPnl(d.abs))
                            .foregroundStyle(direction.color)
                            .contentTransition(.numericText(value: d.abs))
                            .animation(.easeOut(duration: 0.5), value: d.abs)
                    }
                    StatCell(label: "Change", value: fmtPct(d.pct), valueColor: direction.color)
                }
            } else {
                StatGrid {
                    StatCell(label: "Day P&L", value: "—")
                    StatCell(label: "Change", value: "—")
                }
            }
        }
    }

    private func diversificationCard(_ s: ConcentrationStats?) -> some View {
        DashboardInsightCard("Diversification") {
            if let s {
                if s.isEmpty {
                    StatGrid(columns: 3) {
                        StatCell(label: "Score", value: "—")
                    }
                } else {
                    // Green when well-spread, amber mid, red when concentrated.
                    let color = s.score >= 66 ? DS.Palette.success : (s.score >= 33 ? DS.Palette.warning : DS.Palette.danger)
                    StatGrid(columns: 3) {
                        StatCell(label: "Score", value: "\(s.score)/100", valueColor: color)
                        StatCell(label: "Top holding", value: "\(Int(s.topWeight.rounded()))%")
                        StatCell(label: "Holdings", value: "\(s.count)")
                    }
                }
            } else {
                StatGrid(columns: 3) {
                    StatCell(label: "Score", value: "00/100")
                    StatCell(label: "Top holding", value: "00%")
                    StatCell(label: "Holdings", value: "0")
                }
                .redacted(reason: .placeholder)
            }
        }
    }

    @ViewBuilder
    private var riskCard: some View {
        let r = feed.risk[id]
        if r?.isEmpty != true {
            DashboardInsightCard("Risk") {
                StatGrid(columns: 3) {
                    StatCell(label: "Volatility", value: r.map { "\(Int($0.volatility.rounded()))%" } ?? "00%")
                    StatCell(label: "Max drawdown", value: r.map { "\(Int($0.maxDrawdown.rounded()))%" } ?? "00%")
                    StatCell(label: "Sharpe", value: r.map { $0.sharpe.map { dartToStringAsFixed($0, 2) } ?? "—" } ?? "0.00")
                }
                .redacted(reason: r == nil ? .placeholder : [])
            }
        }
    }

    // MARK: Sector allocation

    @ViewBuilder
    private var sectorAllocationSection: some View {
        let slices = feed.sectorAllocation[id]
        // Hide entirely once loaded with nothing (e.g. all cash / no sectors).
        if slices?.isEmpty != true {
            Section {
                Card("Sector allocation") {
                    if let slices {
                        Sector3DChart(slices: slices)
                    } else {
                        Skeleton.circle(180)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 20)
                    }
                }
                .dashboardClearRow()
            }
        }
    }

    // MARK: Market: index cards

    @ViewBuilder
    private var marketSection: some View {
        let quotes = feed.indices
        if quotes?.isEmpty != true {
            Section {
                Group {
                    if let quotes {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 12) {
                                ForEach(quotes) { q in
                                    Button { openStock(q.symbol, brokerageId: nil) } label: {
                                        DashboardIndexCard(quote: q)
                                    }
                                    .buttonStyle(.plain)
                                }
                            }
                        }
                        .scrollClipDisabled()
                    } else {
                        Skeleton(height: DashboardIndexCard.height, radius: DS.Radius.card)
                    }
                }
                .dashboardClearRow()
            } header: {
                DashboardGroupHeader(group: "Market")
            }
        }
    }

    // MARK: Sector performance

    @ViewBuilder
    private var sectorPerformanceSection: some View {
        let quotes = feed.sectorPerformance
        if quotes?.isEmpty != true {
            Section("Sector performance") {
                if let quotes {
                    ForEach(quotes) { q in
                        let color = q.pct >= 0 ? DS.Palette.up : DS.Palette.down
                        HStack(spacing: 12) {
                            Text(q.label)
                                .font(.subheadline)
                                .lineLimit(1)
                                .frame(width: 128, alignment: .leading)
                            // Scaled against a nominal 3% daily move so typical moves show.
                            DashboardBar(fraction: abs(q.pct) / 3.0, color: color)
                            Text(fmtPct(q.pct))
                                .font(.subheadline.monospacedDigit())
                                .foregroundStyle(color)
                                .frame(width: 64, alignment: .trailing)
                        }
                        .accessibilityElement(children: .combine)
                    }
                } else {
                    DashboardPlaceholderRows(count: 3)
                }
            }
        }
    }

    // MARK: Nexus momentum

    @ViewBuilder
    private func momentumSection(_ picks: [MomentumPick]?) -> some View {
        // Only the nexus strategy with momentum enabled produces this.
        if let picks, !picks.isEmpty {
            let maxScore = picks.map { abs($0.score) }.reduce(0) { $0 > $1 ? $0 : $1 }
            Section("Nexus momentum") {
                ForEach(Array(picks.prefix(10).enumerated()), id: \.offset) { _, p in
                    NavigationLink(value: stockRoute(p.symbol, brokerageId: id)) {
                        HStack(spacing: 12) {
                            Text(p.symbol)
                                .font(.headline)
                                .lineLimit(1)
                                .frame(width: 64, alignment: .leading)
                            DashboardBar(
                                fraction: maxScore > 0 ? abs(p.score) / maxScore : 0,
                                color: DS.Palette.accent
                            )
                        }
                    }
                }
            }
        }
    }

    // MARK: Market news

    @ViewBuilder
    private var newsSection: some View {
        let articles = feed.news
        if articles?.isEmpty != true {
            Section("Market news") {
                if let articles {
                    ForEach(Array(articles.prefix(6).enumerated()), id: \.offset) { _, a in
                        Button {
                            // Best-effort: a bad/empty URL silently no-ops.
                            if let url = dashboardBrowserURL(a.url) { openLink(DashboardBrowserLink(url: url)) }
                        } label: {
                            HStack(alignment: .top, spacing: 12) {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(a.title)
                                        .font(.subheadline.weight(.semibold))
                                        .foregroundStyle(Color.primary)
                                        .lineLimit(2)
                                        .multilineTextAlignment(.leading)
                                    Text([a.source.isEmpty ? nil : a.source, a.publishedAt.map { fmtRelative($0) }]
                                        .compactMap { $0 }
                                        .joined(separator: "  ·  "))
                                        .font(.footnote)
                                        .foregroundStyle(.secondary)
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                Image(systemName: "arrow.up.right")
                                    .font(.footnote.weight(.semibold))
                                    .foregroundStyle(.tertiary)
                                    .padding(.top, 2)
                                    .accessibilityHidden(true)
                            }
                            // A list button tints its label; news reads as text.
                            .foregroundStyle(Color.primary)
                            .contentShape(Rectangle())
                        }
                        .accessibilityHint("Opens the article")
                    }
                } else {
                    DashboardPlaceholderRows(count: 2)
                }
            }
        }
    }
}

/// The account screener's gainers and losers (`_MarketMoversCard`): the
/// Gainers / Losers switch sits in the section header, and the rows below
/// show up to five of the chosen side.
private struct DashboardMarketMoversSection: View {
    let data: MoversData?

    @State private var showLosers = false

    var body: some View {
        if !(data.map { $0.gainers.isEmpty && $0.losers.isEmpty } ?? false) {
            Section {
                if let data {
                    let movers = showLosers ? data.losers : data.gainers
                    if movers.isEmpty {
                        Text(showLosers ? "No losers" : "No gainers")
                            .foregroundStyle(.secondary)
                    }
                    ForEach(Array(movers.prefix(5).enumerated()), id: \.offset) { _, m in
                        NavigationLink(value: Route.stock(StockRoute(symbol: m.symbol))) {
                            HStack {
                                Text(m.symbol)
                                    .font(.headline)
                                    .lineLimit(1)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                Text(fmtPct(m.pct))
                                    .font(.body.monospacedDigit())
                                    .foregroundStyle((m.pct ?? 0) >= 0 ? DS.Palette.up : DS.Palette.down)
                            }
                        }
                    }
                } else {
                    DashboardPlaceholderRows(count: 3)
                }
            } header: {
                DSSectionHeader("Market movers") {
                    Picker("Side", selection: $showLosers) {
                        Text("Gainers").tag(false)
                        Text("Losers").tag(true)
                    }
                    .pickerStyle(.segmented)
                    .fixedSize()
                    .controlSize(.small)
                }
            }
        }
    }
}

/// A compact insight card for the horizontal row: a `.headline` title over a
/// `StatGrid`, on the card surface, all the same height.
private struct DashboardInsightCard<Content: View>: View {
    let title: String
    private let content: Content

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: DS.cardGroupSpacing) {
            Text(title)
                .font(.headline)
                .accessibilityAddTraits(.isHeader)
            content
        }
        .padding(DS.cardPadding)
        .frame(width: 300, alignment: .topLeading)
        .frame(maxHeight: .infinity, alignment: .top)
        .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.card, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}

/// One of today's movers among the holdings: the ticker over its move.
private struct DashboardMoverTile: View {
    let mover: Mover

    var body: some View {
        let direction = ChangeDirection(mover.pct)
        VStack(alignment: .leading, spacing: 2) {
            Text(mover.symbol)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(Color.primary)
            HStack(spacing: 2) {
                if let symbol = direction.systemImage {
                    Image(systemName: symbol)
                        .accessibilityHidden(true)
                }
                Text(fmtPct(mover.pct))
            }
            .font(.footnote.weight(.semibold).monospacedDigit())
            .foregroundStyle(direction.color)
        }
        .lineLimit(1)
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}

/// One index card (`_IndexCard`), Stocks-widget style: the name, a
/// sparkline over a dotted opening baseline, the level and today's move.
private struct DashboardIndexCard: View {
    let quote: MarketQuote

    static let height: CGFloat = 136

    var body: some View {
        let direction = ChangeDirection(quote.pct)
        VStack(alignment: .leading, spacing: 0) {
            Text(quote.label)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(Color.primary)
                .lineLimit(1)
            Group {
                if quote.values.count >= 2 {
                    Sparkline(values: quote.values, height: 36, color: direction.color, baseline: quote.values[0])
                } else {
                    Color.clear
                }
            }
            .frame(height: 36)
            .padding(.top, 10)
            Spacer(minLength: 0)
            Text(quote.values.last.map(DashboardFormat.indexLevel) ?? "—")
                .font(.headline.monospacedDigit())
                .foregroundStyle(Color.primary)
                .lineLimit(1)
            HStack(spacing: 2) {
                if let symbol = direction.systemImage {
                    Image(systemName: symbol)
                        .accessibilityHidden(true)
                }
                Text(fmtPct(quote.pct))
            }
            .font(.footnote.weight(.semibold).monospacedDigit())
            .foregroundStyle(direction.color)
            .lineLimit(1)
            .padding(.top, 2)
        }
        .padding(14)
        .frame(width: 148, height: Self.height, alignment: .leading)
        .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.card, style: .continuous))
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}

/// Redacted rows that hold a section's shape while it loads.
struct DashboardPlaceholderRows: View {
    let count: Int

    var body: some View {
        ForEach(0..<count, id: \.self) { _ in
            HStack {
                Text("Placeholder row")
                Spacer()
                Text("+0.00%")
            }
            .redacted(reason: .placeholder)
            .accessibilityHidden(true)
        }
    }
}

extension View {
    /// A list row that shows its own surface (a card, a scrolling row of
    /// cards): no row background, no separator, no row insets.
    func dashboardClearRow() -> some View {
        listRowBackground(Color.clear)
            .listRowSeparator(.hidden)
            .listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 4, trailing: 0))
    }
}
