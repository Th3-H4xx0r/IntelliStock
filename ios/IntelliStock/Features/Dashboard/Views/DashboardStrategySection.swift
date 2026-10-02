import SwiftUI

/// The bot strategy's live telemetry as the dashboard's "Strategy" group —
/// `StrategySection` in `strategy_section.dart` — one list section per card.
/// Each card hides until it has data, and the group (heading included)
/// renders only once at least one card has data; the heading sits over the
/// first card shown. The dashboard starts the fetches 900 ms after it appears
/// (`NexusStrategyModel.arm`), so they don't compete with the core
/// dashboard requests.
struct DashboardStrategySections: View {
    let brokerageId: String
    let model: NexusStrategyModel

    /// The cards, in Dart's order.
    private enum Card: Hashable {
        case trends, recentlyEnded, reversal, backfill, discovered, rationale, outcomes, watchlist
    }

    /// The cards that have data, in order.
    private var visible: [Card] {
        guard model.armed, model.anyData(brokerageId) else { return [] }
        var cards: [Card] = []
        if let view = model.trends[brokerageId], !view.isEmpty {
            if !view.active.isEmpty { cards.append(.trends) }
            if !view.recentlyEnded.isEmpty { cards.append(.recentlyEnded) }
        }
        if model.trends[brokerageId]?.reversalWatch.isEmpty == false { cards.append(.reversal) }
        if model.backfill[brokerageId]?.isEmpty == false { cards.append(.backfill) }
        if model.discovered[brokerageId]?.isEmpty == false { cards.append(.discovered) }
        if model.contexts[brokerageId]?.isEmpty == false { cards.append(.rationale) }
        if let s = model.outcomes[brokerageId], !s.isEmpty { cards.append(.outcomes) }
        if let w = model.watchlist[brokerageId], !w.isEmpty { cards.append(.watchlist) }
        return cards
    }

    var body: some View {
        let cards = visible
        ForEach(cards, id: \.self) { card in
            section(card, group: card == cards.first ? "Strategy" : nil)
        }
    }

    @ViewBuilder
    private func section(_ card: Card, group: String?) -> some View {
        switch card {
        case .trends: trendsSection(group)
        case .recentlyEnded: recentlyEndedSection(group)
        case .reversal: reversalSection(group)
        case .backfill: backfillSection(group)
        case .discovered: discoveredSection(group)
        case .rationale: rationaleSection(group)
        case .outcomes: outcomesSection(group)
        case .watchlist: watchlistSection(group)
        }
    }

    /// A row that opens the symbol's stock screen; an empty symbol opens
    /// nothing (`_open`'s guard).
    @ViewBuilder
    private func stockLink<Label: View>(_ symbol: String, @ViewBuilder label: () -> Label) -> some View {
        if symbol.isEmpty {
            label()
        } else {
            NavigationLink(value: Route.stock(StockRoute(symbol: symbol, brokerageId: brokerageId))) {
                label()
            }
        }
    }

    // MARK: 1. Market trends

    private func trendsSection(_ group: String?) -> some View {
        let active = model.trends[brokerageId]?.active ?? []
        return Section {
            ForEach(Array(active.enumerated()), id: \.offset) { _, t in
                DashboardTrendRow(trend: t, brokerageId: brokerageId)
            }
        } header: {
            DashboardGroupHeader(group: group, title: "Market trends")
        }
    }

    private func recentlyEndedSection(_ group: String?) -> some View {
        let ended = model.trends[brokerageId]?.recentlyEnded ?? []
        return Section {
            ForEach(Array(ended.enumerated()), id: \.offset) { _, t in
                HStack(spacing: 8) {
                    Image(systemName: Symbol.named("check"))
                        .font(.footnote)
                        .foregroundStyle(.tertiary)
                        .accessibilityHidden(true)
                    Text(t.name)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Text(DashboardFormat.endedAgo(t.endedAt))
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                .accessibilityElement(children: .combine)
            }
        } header: {
            DashboardGroupHeader(group: group, title: "Recently ended")
        }
    }

    // MARK: 2. Reversal watch

    private func reversalSection(_ group: String?) -> some View {
        let items = model.trends[brokerageId]?.reversalWatch ?? []
        return Section {
            ForEach(Array(items.enumerated()), id: \.offset) { _, t in
                LabeledContent {
                    Text("\(t.reversalCount) signal\(t.reversalCount == 1 ? "" : "s")")
                        .foregroundStyle(DS.Palette.warning)
                } label: {
                    Text(t.name).lineLimit(1)
                }
            }
        } header: {
            DashboardGroupHeader(group: group, title: "Reversal watch")
        }
    }

    // MARK: 3. Backfill queue

    private func backfillSection(_ group: String?) -> some View {
        let items = model.backfill[brokerageId] ?? []
        return Section {
            ForEach(Array(items.prefix(12).enumerated()), id: \.offset) { _, q in
                stockLink(q.ticker) {
                    EntityRow(q.ticker, subtitle: q.source) {
                        HStack(spacing: 6) {
                            if q.priority {
                                Image(systemName: Symbol.named("push_pin"))
                                    .font(.footnote)
                                    .foregroundStyle(DS.Palette.warning)
                                    .accessibilityLabel("Priority")
                            }
                            if q.nPaths > 0 {
                                Text("\(q.nPaths) paths")
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
        } header: {
            DashboardGroupHeader(group: group, title: "Backfill queue") {
                Text("\(items.count) pending")
            }
        }
    }

    // MARK: 4. Discovered

    private func discoveredSection(_ group: String?) -> some View {
        let items = model.discovered[brokerageId] ?? []
        return Section {
            ForEach(Array(items.prefix(12).enumerated()), id: \.offset) { _, d in
                stockLink(d.ticker) {
                    EntityRow(d.ticker, subtitle: discoveredSource(d))
                }
            }
        } header: {
            DashboardGroupHeader(group: group, title: "Discovered")
        }
    }

    private func discoveredSource(_ d: DiscoveredStock) -> String {
        if let via = d.sourceTicker, !via.isEmpty { return "\(d.source) · via \(via)" }
        return d.source
    }

    // MARK: 5. Bot rationale

    private func rationaleSection(_ group: String?) -> some View {
        let items = model.contexts[brokerageId] ?? []
        return Section {
            ForEach(Array(items.prefix(8).enumerated()), id: \.offset) { _, r in
                stockLink(r.symbol) {
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 8) {
                            Text(r.symbol)
                                .font(.headline)
                            if !r.eventType.isEmpty {
                                Text(r.eventType)
                                    .dsBadge(DS.Palette.accent)
                            }
                        }
                        if !r.reason.isEmpty {
                            Text(r.reason)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                                .multilineTextAlignment(.leading)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityElement(children: .combine)
                }
            }
        } header: {
            DashboardGroupHeader(group: group, title: "Bot rationale")
        }
    }

    // MARK: 6. Outcome scorecard

    @ViewBuilder
    private func outcomesSection(_ group: String?) -> some View {
        if let s = model.outcomes[brokerageId] {
            Section {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("\(Int((s.hitRate * 100).rounded()))%")
                        .font(.title2.weight(.semibold).monospacedDigit())
                        .foregroundStyle(s.hitRate >= 0.5 ? DS.Palette.success : DS.Palette.danger)
                    Text("hit rate · \(s.nCorrect)/\(s.n) signals")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                .accessibilityElement(children: .combine)
                ForEach(Array(s.recent.enumerated()), id: \.offset) { _, o in
                    HStack(spacing: 8) {
                        Image(systemName: Symbol.named(o.correct ? "check" : "close"))
                            .font(.footnote.weight(.bold))
                            .foregroundStyle(o.correct ? DS.Palette.success : DS.Palette.danger)
                            .frame(width: 18)
                            .accessibilityLabel(o.correct ? "Correct" : "Wrong")
                        Text(o.symbol)
                            .font(.headline)
                            .frame(width: 64, alignment: .leading)
                        Text(o.eventType)
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Text("\(o.latestReturn >= 0 ? "+" : "")\(dartToStringAsFixed(o.latestReturn, 1))%")
                            .font(.subheadline.monospacedDigit())
                            .foregroundStyle(o.latestReturn >= 0 ? DS.Palette.success : DS.Palette.danger)
                    }
                    .accessibilityElement(children: .combine)
                }
            } header: {
                DashboardGroupHeader(group: group, title: "Outcome scorecard")
            }
        }
    }

    // MARK: 7. Momentum watchlist

    @ViewBuilder
    private func watchlistSection(_ group: String?) -> some View {
        if let w = model.watchlist[brokerageId] {
            Section {
                DashboardFlowLayout(spacing: 8) {
                    ForEach(Array(w.newest.enumerated()), id: \.offset) { _, e in
                        DashboardTickerTag(
                            symbol: e.symbol,
                            detail: e.firstSeenPrice > 0 ? "@$\(dartToStringAsFixed(e.firstSeenPrice, 0))" : nil,
                            brokerageId: brokerageId
                        )
                    }
                }
                .padding(.vertical, 4)
            } header: {
                DashboardGroupHeader(group: group, title: "Momentum watchlist") {
                    Text("monitoring \(w.count)")
                }
            }
        }
    }
}

/// One active trend: direction, name and strength, the strength bar, and up
/// to five of its tickers, each opening its stock screen.
private struct DashboardTrendRow: View {
    let trend: MarketTrend
    let brokerageId: String

    @Environment(AppServices.self) private var services

    var body: some View {
        let t = trend
        let color = t.bullish ? DS.Palette.up : DS.Palette.down
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Image(systemName: Symbol.named(t.bullish ? "arrow_upward" : "arrow_downward"))
                    .font(.footnote.weight(.bold))
                    .foregroundStyle(color)
                    .accessibilityLabel(t.bullish ? "Bullish" : "Bearish")
                Text(t.name)
                    .font(.headline)
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Text("\(Int((t.strength * 100).rounded()))%")
                    .font(.subheadline.monospacedDigit())
                    .foregroundStyle(color)
            }
            DashboardBar(fraction: t.strength, color: color, height: 4)
            if !t.tickers.isEmpty {
                HStack(spacing: 10) {
                    ForEach(Array(t.tickers.prefix(5).enumerated()), id: \.offset) { _, s in
                        Button(s) {
                            guard !s.isEmpty else { return }
                            services.router.push(.stock(StockRoute(symbol: s, brokerageId: brokerageId)))
                        }
                        .font(.footnote.weight(.semibold))
                        .buttonStyle(.borderless)
                    }
                    if t.tickers.count > 5 {
                        Text("+\(t.tickers.count - 5)")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
        .padding(.vertical, 2)
    }
}

/// A small neutral ticker tag that opens its stock screen (the momentum
/// watchlist).
private struct DashboardTickerTag: View {
    let symbol: String
    let detail: String?
    let brokerageId: String

    @Environment(AppServices.self) private var services

    var body: some View {
        Button {
            guard !symbol.isEmpty else { return }
            services.router.push(.stock(StockRoute(symbol: symbol, brokerageId: brokerageId)))
        } label: {
            HStack(spacing: 4) {
                Text(symbol)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.primary)
                if let detail {
                    Text(detail)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(Color(uiColor: .tertiarySystemFill), in: .rect(cornerRadius: 8, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.borderless)
    }
}
