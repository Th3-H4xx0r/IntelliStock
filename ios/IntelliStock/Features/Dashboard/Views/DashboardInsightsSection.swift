import Charts
import SwiftUI

/// The "Insights" + "Market" block beneath the portfolio — `InsightsSection`
/// in `insights_section.dart`: today's movers, day P&L, diversification,
/// sector allocation and risk for the selected account, then the major
/// indices, sector performance, the account's market movers, nexus momentum
/// and market news. Cards that load empty hide themselves.
struct DashboardInsightsSection: View {
    let scope: DashboardAccountScope
    let feed: DashboardFeedModel

    @Environment(AppServices.self) private var services
    @State private var browserLink: DashboardBrowserLink?

    private var id: String { scope.brokerageId }

    var body: some View {
        let insights = scope.insights
        VStack(alignment: .leading, spacing: 0) {
            Text("Insights")
                .font(.headline)
                .accessibilityAddTraits(.isHeader)
                .padding(.bottom, 14)
            moversStrip(insights.todaysMovers)
            HStack(alignment: .top, spacing: 12) {
                dayPnlTile
                diversificationTile(insights.concentration)
            }
            .padding(.bottom, 12)
            sectorAllocationCard
            riskCard

            Text("Market")
                .font(.headline)
                .accessibilityAddTraits(.isHeader)
                .padding(.top, 20)
                .padding(.bottom, 14)
            indicesStrip
            sectorPerformanceCard
            marketMoversCard(insights.marketMovers)
            momentumCard(insights.momentum)
            newsCard
        }
        .task(id: id) {
            async let account: Void = insights.load(holdings: scope.holdings)
            async let market: Void = feed.loadMarket()
            async let risk: Void = feed.loadRisk(id)
            async let sectors: Void = feed.loadSectorAllocation(id, holdings: { try await scope.holdings.currentHoldings() })
            _ = await (account, market, risk, sectors)
        }
        .task(id: id) { await feed.pollDayChange(id, lifecycle: services.lifecycle) }
        .sheet(item: $browserLink) { link in
            DashboardSafariView(url: link.url).ignoresSafeArea()
        }
    }

    private func openStock(_ symbol: String, brokerageId: String?) {
        services.router.push(.stock(StockRoute(symbol: symbol, brokerageId: brokerageId)))
    }

    // MARK: Today's movers

    @ViewBuilder
    private func moversStrip(_ movers: [Mover]?) -> some View {
        if let movers, !movers.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(movers, id: \.symbol) { m in
                        let up = m.pct >= 0
                        let color = up ? DS.Palette.up : DS.Palette.down
                        Button { openStock(m.symbol, brokerageId: id) } label: {
                            HStack(spacing: 6) {
                                Text(m.symbol)
                                    .foregroundStyle(.primary)
                                Text("\(up ? "▲" : "▼") \(fmtPct(m.pct))")
                                    .foregroundStyle(color)
                            }
                            .font(.caption.weight(.bold).monospacedDigit())
                            .padding(.horizontal, 11)
                            .padding(.vertical, 7)
                            .background(color.opacity(DS.tintFill), in: .rect(cornerRadius: 9, style: .continuous))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .scrollClipDisabled()
            .padding(.bottom, 12)
        }
    }

    // MARK: TODAY + DIVERSIFICATION

    private var dayPnlTile: some View {
        let loaded = feed.dayChangeLoaded(id)
        let d = feed.dayChange[id] ?? nil
        return Card(padding: 14) {
            VStack(alignment: .leading, spacing: 6) {
                DashboardEyebrow("TODAY")
                if !loaded {
                    Skeleton(width: 90, height: 22, radius: 6)
                } else if let d {
                    let up = d.abs >= 0
                    let color = up ? DS.Palette.success : DS.Palette.danger
                    VStack(alignment: .leading, spacing: 2) {
                        Text(fmtPnl(d.abs))
                            .font(.title3.weight(.semibold).monospacedDigit())
                            .foregroundStyle(color)
                            .contentTransition(.numericText(value: d.abs))
                            .animation(.easeOut(duration: 0.5), value: d.abs)
                            .lineLimit(1)
                            .minimumScaleFactor(0.7)
                        HStack(spacing: 3) {
                            Image(systemName: Symbol.named(up ? "trending_up" : "trending_down"))
                                .accessibilityHidden(true)
                            Text(fmtPct(d.pct))
                        }
                        .font(.caption.weight(.bold))
                        .foregroundStyle(color)
                    }
                } else {
                    Text("—").font(.title3.weight(.semibold))
                }
            }
        }
        .accessibilityElement(children: .combine)
    }

    private func diversificationTile(_ s: ConcentrationStats?) -> some View {
        Card(padding: 14) {
            VStack(alignment: .leading, spacing: 6) {
                DashboardEyebrow("DIVERSIFICATION")
                if let s {
                    if s.isEmpty {
                        Text("—").font(.title3.weight(.semibold))
                    } else {
                        // Green when well-spread, amber mid, red when concentrated.
                        let color = s.score >= 66 ? DS.Palette.success : (s.score >= 33 ? DS.Palette.warning : DS.Palette.danger)
                        VStack(alignment: .leading, spacing: 2) {
                            HStack(alignment: .firstTextBaseline, spacing: 0) {
                                Text("\(s.score)")
                                    .font(.title3.weight(.semibold).monospacedDigit())
                                    .foregroundStyle(color)
                                Text(" /100")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Text("Top \(Int(s.topWeight.rounded()))% · \(s.count) holdings")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                    }
                } else {
                    Skeleton(width: 70, height: 22, radius: 6)
                }
            }
        }
        .accessibilityElement(children: .combine)
    }

    // MARK: SECTOR ALLOCATION + RISK

    @ViewBuilder
    private var sectorAllocationCard: some View {
        let slices = feed.sectorAllocation[id]
        // Hide entirely once loaded with nothing (e.g. all cash / no sectors).
        if slices?.isEmpty != true {
            Card(padding: 16) {
                VStack(alignment: .leading, spacing: 8) {
                    DashboardEyebrow("SECTOR ALLOCATION")
                    if let slices {
                        DashboardSectorDonut(slices: slices)
                    } else {
                        Skeleton.circle(180)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 20)
                    }
                }
            }
            .padding(.bottom, 12)
        }
    }

    @ViewBuilder
    private var riskCard: some View {
        let r = feed.risk[id]
        if r?.isEmpty != true {
            Card(padding: 16) {
                VStack(alignment: .leading, spacing: 12) {
                    DashboardEyebrow("RISK")
                    if let r {
                        HStack(alignment: .top) {
                            riskMetric("Volatility", "\(Int(r.volatility.rounded()))%")
                            riskMetric("Max drawdown", "\(Int(r.maxDrawdown.rounded()))%")
                            riskMetric("Sharpe", r.sharpe.map { dartToStringAsFixed($0, 2) } ?? "—")
                        }
                    } else {
                        Skeleton(height: 22, radius: 6)
                    }
                }
            }
            .padding(.bottom, 12)
        }
    }

    private func riskMetric(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(value)
                .font(.subheadline.weight(.bold).monospacedDigit())
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .lineLimit(1)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    // MARK: Market indices

    @ViewBuilder
    private var indicesStrip: some View {
        let quotes = feed.indices
        if quotes?.isEmpty != true {
            Group {
                if let quotes {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 12) {
                            ForEach(quotes) { q in
                                DashboardIndexCard(quote: q) { openStock(q.symbol, brokerageId: nil) }
                            }
                        }
                    }
                    .scrollClipDisabled()
                } else {
                    Skeleton(height: 150, radius: 16)
                }
            }
            .frame(height: 150)
            .padding(.bottom, 12)
        }
    }

    // MARK: Sector performance

    @ViewBuilder
    private var sectorPerformanceCard: some View {
        let quotes = feed.sectorPerformance
        if quotes?.isEmpty != true {
            Card(padding: 16) {
                VStack(alignment: .leading, spacing: 12) {
                    DashboardEyebrow("SECTOR PERFORMANCE")
                    if let quotes {
                        VStack(spacing: 10) {
                            ForEach(quotes) { q in
                                let color = q.pct >= 0 ? DS.Palette.up : DS.Palette.down
                                HStack(spacing: 10) {
                                    Text(q.label)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(1)
                                        .frame(width: 108, alignment: .leading)
                                    // Scaled against a nominal 3% daily move so typical moves show.
                                    DashboardBar(fraction: abs(q.pct) / 3.0, color: color)
                                    Text(fmtPct(q.pct))
                                        .font(.caption.weight(.bold).monospacedDigit())
                                        .foregroundStyle(color)
                                        .frame(width: 58, alignment: .trailing)
                                }
                                .accessibilityElement(children: .combine)
                            }
                        }
                    } else {
                        Skeleton(height: 80, radius: 7)
                    }
                }
            }
            .padding(.bottom, 12)
        }
    }

    // MARK: Market movers

    @ViewBuilder
    private func marketMoversCard(_ data: MoversData?) -> some View {
        if !(data.map { $0.gainers.isEmpty && $0.losers.isEmpty } ?? false) {
            Card(padding: 16) {
                VStack(alignment: .leading, spacing: 12) {
                    DashboardEyebrow("MARKET MOVERS")
                    if let data {
                        HStack(alignment: .top, spacing: 12) {
                            moverColumn("Gainers", data.gainers)
                            moverColumn("Losers", data.losers)
                        }
                    } else {
                        Skeleton(height: 70, radius: 7)
                    }
                }
            }
            .padding(.bottom, 12)
        }
    }

    private func moverColumn(_ title: String, _ movers: [MarketMover]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(title.uppercased())
                .font(.caption2)
                .foregroundStyle(.secondary)
                .padding(.bottom, 6)
            ForEach(Array(movers.prefix(5).enumerated()), id: \.offset) { _, m in
                Button { openStock(m.symbol, brokerageId: nil) } label: {
                    HStack {
                        Text(m.symbol)
                            .font(.caption.weight(.bold))
                            .lineLimit(1)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Text(fmtPct(m.pct))
                            .font(.caption.weight(.bold).monospacedDigit())
                            .foregroundStyle((m.pct ?? 0) >= 0 ? DS.Palette.up : DS.Palette.down)
                    }
                    .padding(.vertical, 4)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Nexus momentum

    @ViewBuilder
    private func momentumCard(_ picks: [MomentumPick]?) -> some View {
        // Only the nexus strategy with momentum enabled produces this.
        if let picks, !picks.isEmpty {
            let maxScore = picks.map { abs($0.score) }.reduce(0) { $0 > $1 ? $0 : $1 }
            Card(padding: 16) {
                VStack(alignment: .leading, spacing: 10) {
                    DashboardCardHeader(symbol: Symbol.named("bolt"), tint: DS.Palette.accent, title: "NEXUS MOMENTUM")
                    VStack(spacing: 0) {
                        ForEach(Array(picks.prefix(10).enumerated()), id: \.offset) { _, p in
                            Button { openStock(p.symbol, brokerageId: id) } label: {
                                HStack(spacing: 0) {
                                    Text(p.symbol)
                                        .font(.caption.weight(.bold))
                                        .lineLimit(1)
                                        .frame(width: 64, alignment: .leading)
                                    DashboardBar(
                                        fraction: maxScore > 0 ? abs(p.score) / maxScore : 0,
                                        color: DS.Palette.accent
                                    )
                                }
                                .padding(.vertical, 5)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
            .padding(.bottom, 12)
        }
    }

    // MARK: Market news

    @ViewBuilder
    private var newsCard: some View {
        let articles = feed.news
        if articles?.isEmpty != true {
            Card(padding: 16) {
                VStack(alignment: .leading, spacing: 8) {
                    DashboardEyebrow("MARKET NEWS")
                    if let articles {
                        VStack(spacing: 0) {
                            ForEach(Array(articles.prefix(6).enumerated()), id: \.offset) { _, a in
                                Button {
                                    // Best-effort: a bad/empty URL silently no-ops.
                                    if let url = dashboardBrowserURL(a.url) { browserLink = DashboardBrowserLink(url: url) }
                                } label: {
                                    HStack(alignment: .top, spacing: 8) {
                                        VStack(alignment: .leading, spacing: 3) {
                                            Text(a.title)
                                                .font(.subheadline.weight(.semibold))
                                                .lineLimit(2)
                                                .multilineTextAlignment(.leading)
                                            Text([a.source.isEmpty ? nil : a.source, a.publishedAt.map { fmtRelative($0) }]
                                                .compactMap { $0 }
                                                .joined(separator: "  ·  "))
                                                .font(.caption2)
                                                .foregroundStyle(.secondary)
                                        }
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                        Image(systemName: Symbol.named("arrow_forward"))
                                            .font(.caption)
                                            .foregroundStyle(.tertiary)
                                            .padding(.top, 2)
                                            .accessibilityHidden(true)
                                    }
                                    .padding(.vertical, 7)
                                    .contentShape(Rectangle())
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    } else {
                        Skeleton(height: 60, radius: 7)
                    }
                }
            }
            .padding(.bottom, 12)
        }
    }
}

/// One index card (`_IndexCard`): label, a sparkline with a dashed opening
/// baseline and an end dot, the level, and today's move.
private struct DashboardIndexCard: View {
    let quote: MarketQuote
    let onTap: () -> Void

    var body: some View {
        let up = quote.pct >= 0
        let color = up ? DS.Palette.up : DS.Palette.down
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 0) {
                Text(quote.label)
                    .font(.subheadline.weight(.bold))
                    .lineLimit(1)
                spark(color)
                    .frame(height: 40)
                    .padding(.top, 10)
                Spacer(minLength: 0)
                Text(quote.values.last.map(DashboardFormat.indexLevel) ?? "—")
                    .font(.subheadline.weight(.heavy).monospacedDigit())
                    .lineLimit(1)
                Text("\(up ? "▲" : "▼") \(fmtPct(quote.pct))")
                    .font(.caption.weight(.bold).monospacedDigit())
                    .foregroundStyle(color)
                    .lineLimit(1)
                    .padding(.top, 2)
            }
            .padding(14)
            .frame(width: 156, height: 150, alignment: .leading)
            .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.card, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private func spark(_ color: Color) -> some View {
        let v = quote.values
        if v.count >= 2 {
            let lo = v.min()!
            let hi = v.max()!
            let span = abs(hi - lo) < 1e-9 ? 1 : hi - lo
            Chart {
                // Dashed baseline at the opening value.
                RuleMark(y: .value("Open", v[0]))
                    .foregroundStyle(Color.secondary.opacity(0.4))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
                ForEach(v.indices, id: \.self) { i in
                    LineMark(x: .value("i", i), y: .value("v", v[i]))
                        .foregroundStyle(color)
                        .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                }
                PointMark(x: .value("i", v.count - 1), y: .value("v", v[v.count - 1]))
                    .foregroundStyle(color)
                    .symbolSize(28)
            }
            .chartXAxis(.hidden)
            .chartYAxis(.hidden)
            .chartLegend(.hidden)
            .chartXScale(domain: 0...(v.count - 1))
            .chartYScale(domain: lo...(lo + span))
            .chartPlotStyle { $0.padding(.vertical, 4) }
            .accessibilityHidden(true)
        } else {
            Color.clear
        }
    }
}
