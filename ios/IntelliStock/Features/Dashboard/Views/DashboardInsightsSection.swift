import SwiftUI

/// The "Insights" and "Market" groups beneath the portfolio — the web's
/// `DashboardInsights` and `DashboardMarket`:
///
/// - **Insights** ("For the selected account"): one card divided into
///   groups — Today, Diversification, Risk · past year, Today's movers — then
///   the sector allocation (the 3D drill-in ring) on a card of its own;
/// - **Market** ("Today's session"): the index tiles two by two, then cards
///   for sector performance, market movers (Gainers / Losers), nexus
///   momentum and market news.
///
/// Cards that load empty hide themselves. The loads run from
/// `DashboardView`'s list; a news tap hands its link back through `openLink`.
/// Rows inside a card are plain buttons: a `NavigationLink` nested in a list
/// row would turn the whole row into one link.
struct DashboardInsightsSections: View {
    let scope: DashboardAccountScope
    let feed: DashboardFeedModel
    let openLink: (DashboardBrowserLink) -> Void

    @Environment(AppServices.self) private var services

    private var id: String { scope.brokerageId }

    var body: some View {
        let insights = scope.insights
        insightsSection(insights)
        marketSection(insights)
    }

    private func openStock(_ symbol: String, brokerageId: String?) {
        guard !symbol.isEmpty else { return }
        services.router.push(.stock(StockRoute(symbol: symbol, brokerageId: brokerageId)))
    }

    // MARK: Insights

    private func insightsSection(_ insights: DashboardAccountInsightsModel) -> some View {
        DSSection("Insights", subtitle: "For the selected account", style: .plain) {
            Card(padding: 0) {
                VStack(spacing: 0) {
                    dayGroup
                    DSDivider()
                    diversificationGroup(insights.concentration)
                    riskGroup
                    if let movers = insights.todaysMovers, !movers.isEmpty {
                        DSDivider()
                        moversGroup(movers)
                    }
                }
            }
            .statValueStyle(.medium)
            .dsClearRow()
            sectorAllocationCard
        }
    }

    private var dayGroup: some View {
        let loaded = feed.dayChangeLoaded(id)
        let d = feed.dayChange[id] ?? nil
        return DashboardInsightGroup("Today") {
            StatGrid(columns: 2) {
                if !loaded {
                    StatCell(label: "Day P&L") { Skeleton(width: 72, height: 18) }
                    StatCell(label: "Change") { Skeleton(width: 56, height: 18) }
                } else if let d {
                    StatCell(label: "Day P&L") {
                        Text(fmtPnl(d.abs))
                            .foregroundStyle(dashboardChangeColor(d.pct ?? d.abs))
                            .contentTransition(.numericText(value: d.abs))
                            .animation(DSMotion.ease(0.5), value: d.abs)
                    }
                    StatCell(label: "Change", value: fmtPct(d.pct), valueColor: dashboardChangeColor(d.pct))
                } else {
                    StatCell(label: "Day P&L", value: "—", valueColor: DS.Text.muted)
                    StatCell(label: "Change", value: "—", valueColor: DS.Text.muted)
                }
            }
        }
    }

    private func diversificationGroup(_ s: ConcentrationStats?) -> some View {
        DashboardInsightGroup("Diversification") {
            StatGrid(columns: 3) {
                if let s {
                    if s.isEmpty {
                        StatCell(label: "Score", value: "—", valueColor: DS.Text.muted)
                        StatCell(label: "Top holding", value: "—")
                        StatCell(label: "Holdings", value: "0")
                    } else {
                        // Gain when well spread, amber mid, loss when concentrated.
                        let color = s.score >= 66 ? DS.Palette.gain : (s.score >= 33 ? DS.Palette.amber : DS.Palette.loss)
                        StatCell(label: "Score", value: "\(s.score)/100", valueColor: color)
                        StatCell(label: "Top holding", value: "\(Int(s.topWeight.rounded()))%")
                        StatCell(label: "Holdings", value: "\(s.count)")
                    }
                } else {
                    StatCell(label: "Score") { Skeleton(width: 56, height: 18) }
                    StatCell(label: "Top holding") { Skeleton(width: 40, height: 18) }
                    StatCell(label: "Holdings") { Skeleton(width: 24, height: 18) }
                }
            }
        }
    }

    @ViewBuilder
    private var riskGroup: some View {
        let r = feed.risk[id]
        if r?.isEmpty != true {
            DSDivider()
            DashboardInsightGroup("Risk · past year") {
                StatGrid(columns: 3) {
                    if let r {
                        StatCell(label: "Volatility", value: "\(Int(r.volatility.rounded()))%")
                        StatCell(label: "Max drawdown", value: "\(Int(r.maxDrawdown.rounded()))%")
                        StatCell(label: "Sharpe", value: r.sharpe.map { dartToStringAsFixed($0, 2) } ?? "—")
                    } else {
                        StatCell(label: "Volatility") { Skeleton(width: 40, height: 18) }
                        StatCell(label: "Max drawdown") { Skeleton(width: 40, height: 18) }
                        StatCell(label: "Sharpe") { Skeleton(width: 40, height: 18) }
                    }
                }
            }
        }
    }

    private func moversGroup(_ movers: [Mover]) -> some View {
        DashboardInsightGroup("Today's movers") {
            VStack(spacing: 0) {
                ForEach(movers.prefix(5), id: \.symbol) { m in
                    Button { openStock(m.symbol, brokerageId: id) } label: {
                        HStack {
                            Text(m.symbol)
                                .font(DS.Font.bodyMedium)
                                .foregroundStyle(DS.Text.soft)
                            Spacer()
                            Text(fmtPct(m.pct))
                                .font(DS.Font.body)
                                .monospacedDigit()
                                .foregroundStyle(dashboardChangeColor(m.pct))
                        }
                        .frame(minHeight: 32)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Opens \(m.symbol)")
                }
            }
        }
    }

    // MARK: Sector allocation

    @ViewBuilder
    private var sectorAllocationCard: some View {
        let slices = feed.sectorAllocation[id]
        // Hide entirely once loaded with nothing (e.g. all cash / no sectors).
        if slices?.isEmpty != true {
            Card("Sector allocation") {
                if let slices {
                    Sector3DChart(slices: slices)
                } else {
                    Skeleton.circle(180)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 20)
                }
            }
            .dsClearRow(top: 16)
        }
    }

    // MARK: Market

    @ViewBuilder
    private func marketSection(_ insights: DashboardAccountInsightsModel) -> some View {
        DSSection("Market", subtitle: "Today's session", style: .plain) {
            indexTiles
            sectorPerformanceCard
            DashboardMarketMoversCard(data: insights.marketMovers, open: { openStock($0, brokerageId: nil) })
            momentumCard(insights.momentum)
            newsCard
        }
    }

    @ViewBuilder
    private var indexTiles: some View {
        let quotes = feed.indices
        if quotes?.isEmpty != true {
            LazyVGrid(columns: [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)], spacing: 12) {
                if let quotes {
                    ForEach(quotes) { q in
                        Button { openStock(q.symbol, brokerageId: nil) } label: {
                            DashboardIndexCard(quote: q)
                        }
                        .buttonStyle(.plain)
                    }
                } else {
                    ForEach(0..<4, id: \.self) { _ in
                        Color.clear
                            .frame(height: DashboardIndexCard.height)
                            .dsCardSurface()
                    }
                }
            }
            .dsClearRow()
        }
    }

    @ViewBuilder
    private var sectorPerformanceCard: some View {
        let quotes = feed.sectorPerformance
        if quotes?.isEmpty != true {
            Card("Sector performance", padding: 0) {
                VStack(spacing: 10) {
                    if let quotes {
                        ForEach(quotes) { q in
                            HStack(spacing: 12) {
                                Text(q.label)
                                    .foregroundStyle(DS.Text.label)
                                    .lineLimit(1)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                // Scaled against a nominal 3% daily move so typical moves show.
                                DashboardBar(fraction: abs(q.pct) / 3.0, color: q.pct >= 0 ? DS.Palette.gain : DS.Palette.loss)
                                    .frame(width: 72)
                                Text(fmtPct(q.pct))
                                    .monospacedDigit()
                                    .foregroundStyle(dashboardChangeColor(q.pct))
                                    .frame(width: 60, alignment: .trailing)
                            }
                            .font(DS.Font.body)
                            .accessibilityElement(children: .combine)
                        }
                    } else {
                        ForEach(0..<6, id: \.self) { _ in Skeleton(height: 16) }
                    }
                }
                .padding(.horizontal, DS.rowPadding)
                .padding(.vertical, 12)
            }
            .dsClearRow(top: 16)
        }
    }

    @ViewBuilder
    private func momentumCard(_ picks: [MomentumPick]?) -> some View {
        // Only the nexus strategy with momentum enabled produces this.
        if let picks, !picks.isEmpty {
            let maxScore = picks.map { abs($0.score) }.reduce(0) { $0 > $1 ? $0 : $1 }
            Card("Nexus momentum", padding: 0) {
                VStack(spacing: 0) {
                    ForEach(Array(picks.prefix(10).enumerated()), id: \.offset) { _, p in
                        Button { openStock(p.symbol, brokerageId: id) } label: {
                            HStack(spacing: 12) {
                                Text(p.symbol)
                                    .font(DS.Font.bodyMedium)
                                    .foregroundStyle(DS.Text.soft)
                                    .lineLimit(1)
                                    .frame(width: 64, alignment: .leading)
                                DashboardBar(fraction: maxScore > 0 ? abs(p.score) / maxScore : 0, color: DS.Text.label)
                            }
                            .frame(minHeight: 32)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, DS.rowPadding)
                .padding(.vertical, 8)
            }
            .dsClearRow(top: 16)
        }
    }

    @ViewBuilder
    private var newsCard: some View {
        let articles = feed.news
        if articles?.isEmpty != true {
            Card("Market news", padding: 0) {
                VStack(spacing: 0) {
                    if let articles {
                        ForEach(Array(articles.prefix(6).enumerated()), id: \.offset) { _, a in
                            Button {
                                // Best-effort: a bad/empty URL silently no-ops.
                                if let url = dashboardBrowserURL(a.url) { openLink(DashboardBrowserLink(url: url)) }
                            } label: {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(a.title)
                                        .font(DS.Font.body)
                                        .foregroundStyle(DS.Text.soft)
                                        .lineLimit(2)
                                        .multilineTextAlignment(.leading)
                                    Text([a.source.isEmpty ? nil : a.source, a.publishedAt.map { fmtRelative($0) }]
                                        .compactMap { $0 }
                                        .joined(separator: " · "))
                                        .font(DS.Font.caption)
                                        .foregroundStyle(DS.Text.muted)
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.horizontal, 12)
                                .padding(.vertical, 10)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .accessibilityHint("Opens the article")
                        }
                    } else {
                        ForEach(0..<4, id: \.self) { _ in
                            Skeleton(height: 32).padding(.horizontal, 12).padding(.vertical, 10)
                        }
                    }
                }
                .padding(8)
            }
            .dsClearRow(top: 16)
        }
    }
}

/// The account screener's gainers and losers (the web's "Market movers"
/// card): the Gainers / Losers control in the card's header, then up to
/// five rows of symbol, price and move.
private struct DashboardMarketMoversCard: View {
    let data: MoversData?
    let open: (String) -> Void

    @State private var showLosers = false

    var body: some View {
        if !(data.map { $0.gainers.isEmpty && $0.losers.isEmpty } ?? false) {
            Card("Market movers", padding: 0) {
                VStack(spacing: 0) {
                    if let data {
                        let movers = showLosers ? data.losers : data.gainers
                        if movers.isEmpty {
                            Text(showLosers ? "No losers" : "No gainers")
                                .font(DS.Font.body)
                                .foregroundStyle(DS.Text.secondary)
                                .frame(maxWidth: .infinity, minHeight: 40, alignment: .leading)
                        }
                        ForEach(Array(movers.prefix(5).enumerated()), id: \.offset) { index, m in
                            if index > 0 { DSDivider(row: true) }
                            Button { open(m.symbol) } label: {
                                HStack(spacing: 12) {
                                    Text(m.symbol)
                                        .font(DS.Font.bodyMedium)
                                        .foregroundStyle(DS.Text.soft)
                                        .lineLimit(1)
                                    Spacer(minLength: 8)
                                    Text(m.price.map { fmtMoney($0) } ?? "—")
                                        .foregroundStyle(DS.Text.secondary)
                                    Text(fmtPct(m.pct))
                                        .foregroundStyle(dashboardChangeColor(m.pct))
                                        .frame(width: 80, alignment: .trailing)
                                }
                                .font(DS.Font.body)
                                .monospacedDigit()
                                .frame(minHeight: 40)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                        }
                    } else {
                        ForEach(0..<4, id: \.self) { _ in Skeleton(height: 16).padding(.vertical, 12) }
                    }
                }
                .padding(.horizontal, DS.rowPadding)
                .padding(.vertical, 4)
            } accessory: {
                DSSegmentedControl("Movers", selection: $showLosers, options: [false, true], segmentHeight: 24) { $0 ? "Losers" : "Gainers" }
            }
            .dsClearRow(top: 16)
        }
    }
}

/// One group in the Insights card: a 12 pt zinc-500 label over its figures,
/// 20 pt in from the card's edges.
private struct DashboardInsightGroup<Content: View>: View {
    let title: String
    private let content: Content

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .accessibilityAddTraits(.isHeader)
            content
        }
        .padding(.horizontal, DS.rowPadding)
        .padding(.vertical, 16)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// One index tile (the web's): the name and ticker, the level and today's
/// move, then a sparkline.
private struct DashboardIndexCard: View {
    let quote: MarketQuote

    static let height: CGFloat = 104

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(quote.label)
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.label)
                    .lineLimit(1)
                Spacer(minLength: 4)
                Text(quote.symbol)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
            }
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(quote.values.last.map { fmtMoney($0) } ?? "—")
                    .font(DS.Font.baseMedium)
                    .foregroundStyle(DS.Text.primary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                Spacer(minLength: 4)
                Text(fmtPct(quote.pct))
                    .font(DS.Font.body)
                    .foregroundStyle(dashboardChangeColor(quote.pct))
                    .lineLimit(1)
            }
            .monospacedDigit()
            .padding(.top, 4)
            Group {
                if quote.values.count >= 2 {
                    Sparkline(values: quote.values, height: 28)
                } else {
                    Color.clear
                }
            }
            .frame(height: 28)
            .padding(.top, 8)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, minHeight: Self.height, alignment: .topLeading)
        .dsCardSurface()
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}

/// Static skeleton rows that hold a section's shape while it loads.
struct DashboardPlaceholderRows: View {
    let count: Int

    var body: some View {
        ForEach(0..<count, id: \.self) { _ in
            HStack {
                Skeleton(width: 120, height: 14)
                Spacer()
                Skeleton(width: 56, height: 14)
            }
            .accessibilityHidden(true)
        }
    }
}
