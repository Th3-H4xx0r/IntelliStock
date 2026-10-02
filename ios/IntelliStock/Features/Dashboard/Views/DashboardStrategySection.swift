import SwiftUI

/// The bot strategy's live telemetry as its own dashboard section —
/// `StrategySection` in `strategy_section.dart`. Each card hides until it
/// has data, and the whole section (header included) renders only once at
/// least one card has data. The dashboard starts the fetches 900 ms after it
/// appears (`NexusStrategyModel.arm`), so they don't compete with the core
/// dashboard requests.
struct DashboardStrategySection: View {
    let brokerageId: String
    let model: NexusStrategyModel

    @Environment(AppServices.self) private var services

    var body: some View {
        Group {
            if model.armed, model.anyData(brokerageId) {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Strategy")
                        .font(.headline)
                        .accessibilityAddTraits(.isHeader)
                        .padding(.bottom, 2)
                    if let view = model.trends[brokerageId] {
                        DashboardMarketTrendsCard(view: view, open: open)
                        DashboardReversalWatchCard(items: view.reversalWatch)
                    }
                    backfillCard
                    discoveredCard
                    rationaleCard
                    outcomesCard
                    watchlistCard
                }
            }
        }
    }

    private func open(_ symbol: String) {
        guard !symbol.isEmpty else { return }
        services.router.push(.stock(StockRoute(symbol: symbol, brokerageId: brokerageId)))
    }

    // MARK: 3. Backfill queue

    @ViewBuilder
    private var backfillCard: some View {
        if let items = model.backfill[brokerageId], !items.isEmpty {
            Card(padding: 16) {
                VStack(alignment: .leading, spacing: 10) {
                    DashboardCardHeader(
                        symbol: Symbol.named("hourglass_empty"), tint: DS.Palette.info,
                        title: "BACKFILL QUEUE", trailing: "\(items.count) pending"
                    )
                    VStack(spacing: 0) {
                        ForEach(Array(items.prefix(12).enumerated()), id: \.offset) { _, q in
                            Button { open(q.ticker) } label: {
                                HStack(spacing: 0) {
                                    if q.priority {
                                        Image(systemName: Symbol.named("push_pin"))
                                            .font(.caption2)
                                            .foregroundStyle(DS.Palette.warning)
                                            .padding(.trailing, 4)
                                            .accessibilityLabel("Priority")
                                    }
                                    Text(q.ticker)
                                        .font(.caption.weight(.bold))
                                        .frame(width: 64, alignment: .leading)
                                    Text(q.source)
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(1)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                    if q.nPaths > 0 {
                                        Text("\(q.nPaths) paths")
                                            .font(.caption2)
                                            .foregroundStyle(.secondary)
                                    }
                                }
                                .padding(.vertical, 5)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
        }
    }

    // MARK: 4. Discovered

    @ViewBuilder
    private var discoveredCard: some View {
        if let items = model.discovered[brokerageId], !items.isEmpty {
            Card(padding: 16) {
                VStack(alignment: .leading, spacing: 10) {
                    DashboardCardHeader(symbol: Symbol.named("search"), tint: DS.Palette.teal, title: "DISCOVERED")
                    VStack(spacing: 0) {
                        ForEach(Array(items.prefix(12).enumerated()), id: \.offset) { _, d in
                            Button { open(d.ticker) } label: {
                                HStack(spacing: 0) {
                                    Text(d.ticker)
                                        .font(.caption.weight(.bold))
                                        .frame(width: 64, alignment: .leading)
                                    Text(discoveredSource(d))
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(1)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                }
                                .padding(.vertical, 5)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
        }
    }

    private func discoveredSource(_ d: DiscoveredStock) -> String {
        if let via = d.sourceTicker, !via.isEmpty { return "\(d.source) · via \(via)" }
        return d.source
    }

    // MARK: 5. Bot rationale

    @ViewBuilder
    private var rationaleCard: some View {
        if let items = model.contexts[brokerageId], !items.isEmpty {
            Card(padding: 16) {
                VStack(alignment: .leading, spacing: 10) {
                    DashboardCardHeader(symbol: Symbol.named("psychology"), tint: DS.Palette.accent, title: "BOT RATIONALE")
                    VStack(spacing: 0) {
                        ForEach(Array(items.prefix(8).enumerated()), id: \.offset) { _, r in
                            Button { open(r.symbol) } label: {
                                VStack(alignment: .leading, spacing: 3) {
                                    HStack(spacing: 8) {
                                        Text(r.symbol)
                                            .font(.caption.weight(.bold))
                                        if !r.eventType.isEmpty {
                                            DashboardTintTag(text: r.eventType, color: DS.Palette.accent, weight: .regular)
                                        }
                                    }
                                    if !r.reason.isEmpty {
                                        Text(r.reason)
                                            .font(.caption2)
                                            .foregroundStyle(.secondary)
                                            .lineLimit(2)
                                            .multilineTextAlignment(.leading)
                                    }
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.vertical, 6)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
        }
    }

    // MARK: 6. Outcome scorecard

    @ViewBuilder
    private var outcomesCard: some View {
        if let s = model.outcomes[brokerageId], !s.isEmpty {
            Card(padding: 16) {
                VStack(alignment: .leading, spacing: 8) {
                    DashboardCardHeader(symbol: Symbol.named("score"), tint: DS.Palette.info, title: "OUTCOME SCORECARD")
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text("\(Int((s.hitRate * 100).rounded()))%")
                            .font(.title3.weight(.semibold).monospacedDigit())
                            .foregroundStyle(s.hitRate >= 0.5 ? DS.Palette.success : DS.Palette.danger)
                        Text("hit rate · \(s.nCorrect)/\(s.n) signals")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.top, 2)
                    VStack(spacing: 6) {
                        ForEach(Array(s.recent.enumerated()), id: \.offset) { _, o in
                            HStack(spacing: 6) {
                                Image(systemName: Symbol.named(o.correct ? "check" : "close"))
                                    .font(.caption2.weight(.bold))
                                    .foregroundStyle(o.correct ? DS.Palette.success : DS.Palette.danger)
                                    .accessibilityLabel(o.correct ? "Correct" : "Wrong")
                                Text(o.symbol)
                                    .font(.caption2.weight(.bold))
                                    .frame(width: 56, alignment: .leading)
                                Text(o.eventType)
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                Text("\(o.latestReturn >= 0 ? "+" : "")\(dartToStringAsFixed(o.latestReturn, 1))%")
                                    .font(.caption2.monospacedDigit())
                                    .foregroundStyle(o.latestReturn >= 0 ? DS.Palette.success : DS.Palette.danger)
                            }
                            .accessibilityElement(children: .combine)
                        }
                    }
                }
            }
        }
    }

    // MARK: 7. Momentum watchlist

    @ViewBuilder
    private var watchlistCard: some View {
        if let w = model.watchlist[brokerageId], !w.isEmpty {
            Card(padding: 16) {
                VStack(alignment: .leading, spacing: 10) {
                    DashboardCardHeader(
                        symbol: Symbol.named("visibility"), tint: DS.Palette.teal,
                        title: "MOMENTUM WATCHLIST", trailing: "monitoring \(w.count)"
                    )
                    DashboardFlowLayout(spacing: 8) {
                        ForEach(Array(w.newest.enumerated()), id: \.offset) { _, e in
                            Button { open(e.symbol) } label: {
                                HStack(spacing: 5) {
                                    Text(e.symbol)
                                        .font(.caption2.weight(.bold))
                                    if e.firstSeenPrice > 0 {
                                        Text("@$\(dartToStringAsFixed(e.firstSeenPrice, 0))")
                                            .font(.caption2)
                                            .foregroundStyle(.secondary)
                                    }
                                }
                                .padding(.horizontal, 9)
                                .padding(.vertical, 6)
                                .background(DS.Palette.teal.opacity(DS.tintFill), in: .rect(cornerRadius: 8, style: .continuous))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
        }
    }
}

/// 1. MARKET TRENDS — active trends with strength bars and tickers, then
/// the recently ended ones (`_MarketTrendsCard`).
struct DashboardMarketTrendsCard: View {
    let view: NexusTrendsView
    let open: (String) -> Void

    var body: some View {
        if !view.isEmpty {
            Card(padding: 16) {
                VStack(alignment: .leading, spacing: 10) {
                    DashboardCardHeader(symbol: Symbol.named("trending_up"), tint: DS.Palette.accent, title: "MARKET TRENDS")
                    VStack(spacing: 0) {
                        ForEach(Array(view.active.enumerated()), id: \.offset) { _, t in
                            trendRow(t)
                        }
                    }
                    if !view.recentlyEnded.isEmpty {
                        DashboardEyebrow("RECENTLY ENDED")
                            .padding(.top, 6)
                        VStack(spacing: 0) {
                            ForEach(Array(view.recentlyEnded.enumerated()), id: \.offset) { _, t in
                                HStack(spacing: 6) {
                                    Image(systemName: Symbol.named("check"))
                                        .font(.caption2)
                                        .foregroundStyle(.tertiary)
                                        .accessibilityHidden(true)
                                    Text(t.name)
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(1)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                    Text(DashboardFormat.endedAgo(t.endedAt))
                                        .font(.caption2)
                                        .foregroundStyle(.tertiary)
                                }
                                .padding(.vertical, 3)
                            }
                        }
                    }
                }
            }
        }
    }

    private func trendRow(_ t: MarketTrend) -> some View {
        let color = t.bullish ? DS.Palette.up : DS.Palette.down
        return VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: Symbol.named(t.bullish ? "arrow_upward" : "arrow_downward"))
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(color)
                    .accessibilityLabel(t.bullish ? "Bullish" : "Bearish")
                Text(t.name)
                    .font(.caption.weight(.bold))
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Text("\(Int((t.strength * 100).rounded()))%")
                    .font(.caption2.weight(.bold).monospacedDigit())
                    .foregroundStyle(color)
            }
            DashboardBar(fraction: t.strength, color: color, height: 4)
            if !t.tickers.isEmpty {
                HStack(spacing: 6) {
                    ForEach(Array(t.tickers.prefix(5).enumerated()), id: \.offset) { _, s in
                        Button(s) { open(s) }
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .buttonStyle(.plain)
                    }
                    if t.tickers.count > 5 {
                        Text("+\(t.tickers.count - 5)")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                }
                .padding(.top, 1)
            }
        }
        .padding(.vertical, 6)
    }
}

/// 2. REVERSAL WATCH — active trends with reversal signals.
struct DashboardReversalWatchCard: View {
    let items: [MarketTrend]

    var body: some View {
        if !items.isEmpty {
            Card(padding: 16) {
                VStack(alignment: .leading, spacing: 10) {
                    DashboardCardHeader(symbol: Symbol.named("warning"), tint: DS.Palette.warning, title: "REVERSAL WATCH")
                    VStack(spacing: 0) {
                        ForEach(Array(items.enumerated()), id: \.offset) { _, t in
                            HStack {
                                Text(t.name)
                                    .font(.caption)
                                    .lineLimit(1)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                Text("\(t.reversalCount) signal\(t.reversalCount == 1 ? "" : "s")")
                                    .font(.caption2)
                                    .foregroundStyle(DS.Palette.warning)
                            }
                            .padding(.vertical, 5)
                        }
                    }
                }
            }
        }
    }
}

/// A left-aligned wrapping row of chips (Dart `Wrap`).
struct DashboardFlowLayout: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        var maxX: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > 0, x + size.width > width {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            x += size.width + spacing
            maxX = max(maxX, x - spacing)
            rowHeight = max(rowHeight, size.height)
        }
        return CGSize(width: proposal.width ?? maxX, height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX
        var y = bounds.minY
        var rowHeight: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > bounds.minX, x + size.width > bounds.maxX {
                x = bounds.minX
                y += rowHeight + spacing
                rowHeight = 0
            }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}
