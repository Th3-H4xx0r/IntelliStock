import SwiftUI

// The sections of the backtest detail screen (backtest_detail_screen.dart's
// private widgets), each a native card.

/// An upper-cased card title (`AppTextStyles.eyebrow`).
private struct BacktestEyebrow: View {
    let text: String

    var body: some View {
        Text(text.uppercased())
            .font(.footnote.weight(.semibold))
            .foregroundStyle(.secondary)
            .accessibilityAddTraits(.isHeader)
    }
}

// MARK: - LLM pause banner

/// Shown only when the status is `paused_llm_critical` —
/// `BacktestLlmPauseBanner`.
struct BacktestLlmPauseBanner: View {
    let summary: BacktestSummary

    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        if (summary.status ?? "").lowercased() == "paused_llm_critical" {
            let ink = DS.Palette.onTint(DS.Palette.warning, in: colorScheme)
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: Symbol.named("pause_circle_outline"))
                    .font(.title3)
                    .foregroundStyle(ink)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Backtest paused: LLM critical failure")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(ink)
                        .padding(.bottom, 5)
                    row("Bar", BacktestDetailFormat.fmtBar(summary.pauseBarTime))
                    row("Reason", "\(summary.pauseReasonTag ?? "unknown")  (\(BacktestDetailFormat.numText(summary.pauseAttempts, "?")) attempts)")
                    row("Provider", "\(summary.pauseProvider ?? "?")  •  Model: \(summary.pauseModel ?? "?")")
                    row("Call site", summary.pauseCallSite ?? "unknown")
                    row("Paused at", BacktestDetailFormat.fmtPausedAt(summary.pausedAt))
                    if let sample = summary.pauseSample, !sample.isEmpty {
                        Text(sample)
                            .font(.caption2.monospaced())
                            .foregroundStyle(ink.opacity(0.8))
                            .textSelection(.enabled)
                            .padding(8)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(DS.Surface.inset, in: .rect(cornerRadius: 6, style: .continuous))
                            .padding(.top, 5)
                    }
                    Text("Tap \"Resume\" above when the provider is healthy again. The same bar will retry from the snapshot.")
                        .font(.footnote)
                        .foregroundStyle(ink.opacity(0.8))
                        .padding(.top, 5)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(DS.Palette.warning.opacity(DS.tintFill), in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
        }
    }

    private func row(_ label: String, _ value: String) -> some View {
        let l = Text("\(label):  ").foregroundStyle(DS.Palette.onTint(DS.Palette.warning, in: colorScheme).opacity(0.8))
        let v = Text(value).foregroundStyle(.primary)
        return Text("\(l)\(v)").font(.footnote)
    }
}

// MARK: - Nexus lookback banner

struct BacktestNexusLookbackBanner: View {
    let lookback: NexusLookback

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Image(systemName: Symbol.named("hub")).foregroundStyle(.tint)
                Text("Nexus Lookback Training").font(.subheadline.weight(.semibold)).foregroundStyle(.tint)
                Spacer()
                Text("Day \(lookback.current) / \(lookback.total)")
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
            }
            ProgressView(value: min(max(lookback.fraction, 0), 1))
                .tint(DS.Palette.accent)
                .padding(.vertical, 4)
            HStack {
                Text(lookback.startDate ?? "").foregroundStyle(.secondary)
                Spacer()
                Text(lookback.currentDate ?? "").foregroundStyle(.tint)
                Spacer()
                Text(lookback.endDate ?? "").foregroundStyle(.secondary)
            }
            .font(.caption2)
            Text("Building historical event context before trading begins. This runs once per scope.")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(16)
        .background(DS.Palette.accent.opacity(DS.tintFill), in: .rect(cornerRadius: 16, style: .continuous))
    }
}

// MARK: - Stat grid

struct BacktestStatGrid: View {
    let summary: BacktestSummary
    let elapsed: Num?

    var body: some View {
        let s = summary
        let winRate = s.winRatePercent
        let tiles: [(String, String, Color, String?)] = [
            ("Total P&L", fmtPnl(s.pnl?.double), pnlColor(s.pnl?.double), nil),
            ("P&L %", fmtPct(s.pnlPercent?.double), pnlColor(s.pnlPercent?.double), nil),
            ("Portfolio", fmtMoney(s.portfolioEndValue?.double),
             pnlColor((s.portfolioEndValue?.double ?? 0) - (s.portfolioStartValue?.double ?? 0)),
             "From \(fmtMoney(s.portfolioStartValue?.double))"),
            ("Trades", BacktestDetailFormat.numText(s.totalTrades, "—"), .primary,
             "\(BacktestDetailFormat.numText(s.totalBuys, "0")) buy / \(BacktestDetailFormat.numText(s.totalSells, "0")) sell"),
            ("Elapsed", fmtElapsed(elapsed?.double), .primary, nil),
            ("Win Rate", winRate.map { "\(dartToStringAsFixed($0.double, 1))%" } ?? "—",
             (winRate?.double ?? 0) >= 50 ? DS.Palette.success : DS.Palette.danger,
             "\(BacktestDetailFormat.numText(s.winningRoundTrips, "0"))W / \(BacktestDetailFormat.numText(s.losingRoundTrips, "0"))L"),
            ("Portfolio High", fmtMoney(s.portfolioValueHigh?.double), DS.Palette.success,
             "Low: \(fmtMoney(s.portfolioValueLow?.double))"),
        ]
        LazyVGrid(columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)], spacing: 10) {
            ForEach(tiles.indices, id: \.self) { i in
                StatTile(label: tiles[i].0, value: tiles[i].1, valueColor: tiles[i].2, sub: tiles[i].3)
            }
        }
    }
}

// MARK: - AI credits

struct BacktestLlmCostCard: View {
    let llmCost: LlmCost?
    let loading: Bool
    let error: String?
    let onRefresh: () -> Void

    var body: some View {
        Card(padding: 0) {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 10) {
                    IconTile(systemImage: Symbol.named("payments"), size: 32)
                    VStack(alignment: .leading, spacing: 0) {
                        BacktestEyebrow(text: "AI Credits")
                        Text("LLM cost for this backtest").font(.footnote).foregroundStyle(.secondary)
                    }
                    Spacer()
                    if loading {
                        ProgressView()
                    } else {
                        Button(action: onRefresh) {
                            Image(systemName: Symbol.named("refresh"))
                                .frame(width: 44, height: 44)
                        }
                        .buttonStyle(.borderless)
                        .accessibilityLabel("Refresh AI credits")
                    }
                }
                .padding(EdgeInsets(top: 10, leading: 16, bottom: 10, trailing: 8))
                Divider()
                if let error {
                    Text(error).font(.footnote).foregroundStyle(DS.Palette.danger).padding(16)
                } else if llmCost == nil, loading {
                    VStack(alignment: .leading, spacing: 8) {
                        Skeleton.line(height: 13)
                        Skeleton.line(height: 10)
                        Skeleton.line(height: 10)
                    }
                    .padding(16)
                } else if let cost = llmCost, (cost.totalCalls?.double ?? 0) != 0 {
                    totals(cost)
                    Divider()
                    breakdown(cost)
                } else {
                    Text("No LLM calls were attributed to this backtest.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .padding(16)
                }
            }
        }
    }

    private func totals(_ c: LlmCost) -> some View {
        Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 16) {
            GridRow {
                tile("Total cost", fmtUsdCost(c.totalCostUsd?.double))
                tile("Calls", BacktestDetailFormat.numText(c.totalCalls, "0"),
                     sub: "\(BacktestDetailFormat.numText(c.okCalls, "0")) ok · \(BacktestDetailFormat.numText(c.failedCalls, "0")) failed")
            }
            GridRow {
                tile("Input tokens", tokens(c.totalInputTokens))
                tile("Output tokens", tokens(c.totalOutputTokens))
            }
        }
        .padding(16)
    }

    private func tokens(_ n: Num?) -> String {
        switch n {
        case .int(let i): fmtTokens(i)
        case .double(let d): fmtTokens(d)
        case nil: fmtTokens(Int?.none)
        }
    }

    private func tile(_ label: String, _ value: String, sub: String? = nil) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            BacktestEyebrow(text: label)
            Text(value).font(.title3.weight(.bold).monospacedDigit())
            if let sub { Text(sub).font(.caption2).foregroundStyle(.secondary) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func breakdown(_ c: LlmCost) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            group("By model", c.byModel)
            group("By call site", c.byCallSite)
            group("By provider", c.byProvider)
        }
        .padding(16)
    }

    @ViewBuilder
    private func group(_ title: String, _ rows: [LlmCostRow]) -> some View {
        if !rows.isEmpty {
            BacktestEyebrow(text: title)
            ForEach(Array(rows.prefix(6).enumerated()), id: \.offset) { _, r in
                HStack {
                    Text(r.key).foregroundStyle(.secondary).lineLimit(1)
                    Spacer()
                    Text(fmtUsdCost(r.costUsd?.double))
                }
                .font(.caption.monospaced())
            }
            .padding(.bottom, 2)
        }
    }
}

// MARK: - Crypto fees

/// Per-platform fee estimate (crypto backtests only) — `_CryptoFeesCard`.
struct BacktestCryptoFeesCard: View {
    let summary: BacktestSummary

    var body: some View {
        let fees = summary.fees
        let volume = fees?["total_volume"]?.double ?? 0
        if let fees, volume > 0 {
            let actual = fees["total_fees"]?.double ?? 0
            let appliedRate = fees["taker_rate"]?.double ?? 0.0025
            let emulated = summary.feeEmulated == true
            let appliedLabel = BacktestDetailFormat.appliedLabel(venue: summary.feeVenue, rate: appliedRate)
            Card {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 8) {
                        Image(systemName: Symbol.named("receipt_long")).foregroundStyle(.tint)
                        BacktestEyebrow(text: "Fees · crypto")
                        Spacer()
                        if emulated {
                            MarketsTag(text: "Emulated · \(appliedLabel)", color: DS.Palette.warning)
                        }
                    }
                    HStack {
                        Text("Crypto volume traded").font(.footnote).foregroundStyle(.secondary)
                        Spacer()
                        Text(fmtMoney(volume)).font(.subheadline.weight(.semibold).monospacedDigit())
                    }
                    .padding(.top, 4)
                    Divider().padding(.vertical, 4)
                    ForEach(BacktestDetailFormat.feePlatforms, id: \.id) { p in
                        let applied = abs(p.rate - appliedRate) < 1e-9
                        HStack(spacing: 10) {
                            HStack(spacing: 6) {
                                Text(p.name).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                                if applied { MarketsTag(text: "applied") }
                            }
                            Spacer()
                            Text("\(dartToStringAsFixed(p.rate * 100, 2))%")
                                .font(.caption2.monospacedDigit())
                                .foregroundStyle(.secondary)
                                .frame(width: 52, alignment: .trailing)
                            Text(fmtMoney(applied ? (actual > 0 ? actual : volume * p.rate) : volume * p.rate))
                                .font(.subheadline.weight(.semibold).monospacedDigit())
                                .foregroundStyle(applied ? Color.primary : Color.secondary)
                                .frame(width: 84, alignment: .trailing)
                        }
                    }
                    Text(emulated
                         ? "\(appliedLabel) fees were emulated here (not your instance's brokerage) — the \"applied\" row. Other venues are estimates (volume × rate); actual tiers vary."
                         : "\(appliedLabel) is the fee actually charged in this backtest; other venues are estimates at their listed taker rate (volume × rate) — actual tiers vary.")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
        }
    }
}

// MARK: - Strategy

struct BacktestStrategySection: View {
    let schema: StrategySchema
    let strategyId: String?
    @Binding var open: Bool

    var body: some View {
        Card(padding: 0) {
            VStack(spacing: 0) {
                Button {
                    withAnimation(.snappy) { open.toggle() }
                } label: {
                    HStack(spacing: 10) {
                        IconTile(systemImage: Symbol.named("schema"), size: 32)
                        VStack(alignment: .leading, spacing: 0) {
                            BacktestEyebrow(text: "Strategy")
                            Text(schema.name ?? "—").font(.subheadline.weight(.semibold)).foregroundStyle(.primary)
                        }
                        Spacer()
                        Image(systemName: Symbol.named("expand_more"))
                            .foregroundStyle(.secondary)
                            .rotationEffect(.degrees(open ? 180 : 0))
                    }
                    .padding(16)
                    .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .accessibilityHint(open ? "Collapses the strategy" : "Shows the strategy")
                if open {
                    Divider()
                    VStack(alignment: .leading, spacing: 8) {
                        if let strategyId {
                            Text("ID: \(strategyId)").font(.caption2).foregroundStyle(.tertiary)
                        }
                        BacktestEyebrow(text: "Sub-strategies")
                        ForEach(Array(schema.strategies.enumerated()), id: \.offset) { i, sub in
                            subStrategy(i, sub)
                        }
                        if schema.strategies.isEmpty {
                            Text("No sub-strategies defined").font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                    .padding(16)
                }
            }
        }
    }

    private func subStrategy(_ index: Int, _ sub: BacktestSubStrategy) -> some View {
        // `{...conditions, ...config}`: config overrides a shared key in place.
        var combined = sub.conditions.entries.map { ($0.key, $0.value) }
        for e in sub.config.entries {
            if let i = combined.firstIndex(where: { $0.0 == e.key }) { combined[i].1 = e.value } else { combined.append((e.key, e.value)) }
        }
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text("\(index + 1)")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(.tint)
                    .frame(width: 20, height: 20)
                    .background(DS.Palette.accent.opacity(DS.tintFill), in: .rect(cornerRadius: 4, style: .continuous))
                Text(sub.strategy ?? "?").font(.subheadline.weight(.semibold))
                Spacer()
                if let w = sub.weight { MarketsChip(text: "\(dartToStringAsFixed(w.double * 100, 0))%", color: .secondary) }
                if let phase = sub.decisionPhase { MarketsChip(text: phase, color: .secondary) }
            }
            if !combined.isEmpty {
                MarketsFlowLayout(spacing: 6, runSpacing: 4) {
                    ForEach(Array(combined.enumerated()), id: \.offset) { _, kv in
                        VStack(alignment: .leading, spacing: 0) {
                            Text(kv.0).font(.caption2).foregroundStyle(.tertiary)
                            Text(kv.1.dartDescription).font(.caption.monospaced()).foregroundStyle(.secondary)
                        }
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(DS.Surface.canvas, in: .rect(cornerRadius: 6, style: .continuous))
                    }
                }
            }
        }
        .padding(12)
        .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
    }
}

// MARK: - Logs

struct BacktestLogsPanel: View {
    let id: String
    let open: Bool
    let lines: [String]
    let loading: Bool
    let error: String?
    let source: String
    let onToggle: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Circle()
                    .fill(open ? DS.Palette.success : Color(uiColor: .quaternaryLabel))
                    .frame(width: 8, height: 8)
                Text("backtest-\(id).log").font(.caption.monospaced()).lineLimit(1)
                if !lines.isEmpty {
                    Text("\(lines.count) lines").font(.caption2).foregroundStyle(.tertiary)
                }
                if source == "db" {
                    Text("(last 500)").font(.caption2).foregroundStyle(DS.Palette.warning)
                }
                Spacer()
                Button(open ? "Hide Logs" : "View Logs", action: onToggle)
                    .font(.caption.weight(.semibold))
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .tint(open ? DS.Palette.info : .secondary)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            if open {
                Divider()
                Group {
                    if loading {
                        LoadingState(label: "Loading logs…").padding(16)
                    } else if let error {
                        Text(error).font(.footnote).foregroundStyle(DS.Palette.danger).padding(12)
                    } else if lines.isEmpty {
                        Text("No logs available.").font(.footnote).foregroundStyle(.secondary).padding(12)
                    } else {
                        ScrollView {
                            LazyVStack(alignment: .leading, spacing: 2) {
                                ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                                    Text(line)
                                        .font(.system(.caption2, design: .monospaced))
                                        .foregroundStyle(BacktestDetailFormat.levelColor(line))
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                }
                            }
                            .padding(8)
                            .textSelection(.enabled)
                        }
                        .frame(maxHeight: 400)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.card, style: .continuous))
    }
}

// MARK: - Portfolio chart

struct BacktestPortfolioChart: View {
    let history: [PortfolioValuePoint]
    let startValue: Double?

    @State private var scrubIdx: Int?

    var body: some View {
        let timestamps = history.map(\.timestamp)
        let values = history.map(\.value)
        let startVal = startValue ?? values.first ?? 0
        let isUp = !values.isEmpty && values.last! >= startVal
        let i = scrubIdx.flatMap { $0 >= 0 && $0 < values.count ? $0 : nil }
        let display = i.map { values[$0] } ?? values.last ?? 0
        let pnl = display - startVal
        Card(padding: EdgeInsets(top: 14, leading: 16, bottom: 12, trailing: 16)) {
            VStack(alignment: .leading, spacing: 4) {
                BacktestEyebrow(text: "Portfolio Value Over Time")
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(fmtMoney(display))
                        .font(.title2.bold().monospacedDigit())
                        .contentTransition(.numericText(value: display))
                    Text(fmtPnl(pnl)).font(.subheadline.monospacedDigit()).foregroundStyle(pnlColor(pnl))
                    Text("vs start").font(.footnote).foregroundStyle(.secondary)
                }
                Text(i.map { fmtDateTime(timestamps[$0]) } ?? " ")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                ScrubbableAreaChart(
                    timestamps: timestamps,
                    values: values,
                    lineColor: isUp ? DS.Palette.success : DS.Palette.danger,
                    height: 240,
                    baseline: startValue,
                    onScrub: { scrubIdx = $0 }
                )
                .padding(.top, 8)
            }
        }
    }
}

// MARK: - P&L per stock

struct BacktestPnlPerStockCard: View {
    let summary: BacktestSummary

    var body: some View {
        Card(padding: 0) {
            VStack(alignment: .leading, spacing: 0) {
                BacktestEyebrow(text: "P&L per Stock").padding(16)
                ForEach(summary.tickers, id: \.self) { sym in
                    let p = summary.pnlPerStock?[sym]?.double
                    let pp = summary.pnlPercentPerStock?[sym]?.double
                    let pc = summary.stockPriceChange?[sym]?.double
                    Divider()
                    HStack(spacing: 12) {
                        Text(sym).font(.footnote.monospaced().weight(.bold))
                        Spacer()
                        Text(fmtPnl(p)).font(.footnote.monospaced().weight(.semibold)).foregroundStyle(pnlColor(p))
                        Text(fmtPct(pp)).font(.caption.monospaced()).foregroundStyle(pnlColor(pp))
                        Text(fmtPct(pc)).font(.caption.monospaced()).foregroundStyle(pnlColor(pc))
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
                }
            }
        }
    }
}

// MARK: - Stock accordion

struct BacktestStockAccordion: View {
    let sym: String
    let summary: BacktestSummary
    let graphData: BacktestGraphData
    let expanded: Bool
    let decisionLimit: Int
    let onToggle: () -> Void
    let onShowMoreDecisions: () -> Void

    private var trades: [BacktestTrade] {
        graphData.backtestTrades.filter { $0.ticker == sym }
            .enumerated()
            .sorted { a, b in
                let ta = a.element.timestamp ?? .distantPast
                let tb = b.element.timestamp ?? .distantPast
                return ta != tb ? ta < tb : a.offset < b.offset
            }
            .map(\.element)
    }

    var body: some View {
        let pnl = summary.pnlPerStock?[sym]?.double
        let pnlPct = summary.pnlPercentPerStock?[sym]?.double
        let trades = trades
        VStack(spacing: 0) {
            Button(action: onToggle) {
                HStack(spacing: 8) {
                    Text(sym).font(.subheadline.monospaced().weight(.bold)).foregroundStyle(.primary)
                    Text("\(fmtPnl(pnl)) (\(fmtPct(pnlPct)))").font(.footnote).foregroundStyle(pnlColor(pnl))
                    Text("\(trades.count) trades").font(.caption2).foregroundStyle(.tertiary)
                    Spacer()
                    Image(systemName: Symbol.named("expand_more"))
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .rotationEffect(.degrees(expanded ? 180 : 0))
                }
                .padding(14)
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            if expanded {
                expandedContent(trades)
            }
        }
        .background(DS.Surface.panel, in: .rect(cornerRadius: 16, style: .continuous))
    }

    @ViewBuilder
    private func expandedContent(_ trades: [BacktestTrade]) -> some View {
        let prices = graphData.backtestPrices.filter { $0.symbol == sym }.sorted { $0.timestamp < $1.timestamp }
        let decisions = graphData.backtestDecisions.filter { $0.symbol == sym }
            .enumerated()
            .sorted { a, b in
                let ta = a.element.timestamp ?? .distantPast
                let tb = b.element.timestamp ?? .distantPast
                return ta != tb ? ta > tb : a.offset < b.offset
            }
            .map(\.element)
        Divider()
        if !prices.isEmpty {
            let buys = trades.filter { ($0.action ?? "").lowercased().contains("buy") }
            let sells = trades.filter { ($0.action ?? "").lowercased().contains("sell") }
            let markers = buys.map { ScrubbableChartMarker(date: $0.timestamp ?? Date(), value: $0.price?.double ?? 0, color: DS.Palette.success) }
                + sells.map { ScrubbableChartMarker(date: $0.timestamp ?? Date(), value: $0.price?.double ?? 0, color: DS.Palette.danger) }
            ScrubbableAreaChart(
                timestamps: prices.map(\.timestamp),
                values: prices.map(\.close),
                lineColor: DS.Palette.info,
                height: 220,
                markers: markers
            )
            .padding(12)
        }
        if !trades.isEmpty {
            Divider()
            BacktestTradeTable(trades: trades)
        }
        if !decisions.isEmpty {
            BacktestDecisionTrace(decisions: decisions, limit: decisionLimit, onShowMore: onShowMoreDecisions)
        }
    }
}

/// The trade table (Time / Action / Shares / Price / Total / Cash After),
/// scrolling sideways like the Dart `DataTable`.
struct BacktestTradeTable: View {
    let trades: [BacktestTrade]

    var body: some View {
        ScrollView(.horizontal) {
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 8) {
                GridRow {
                    ForEach(["Time", "Action", "Shares", "Price", "Total", "Cash After"], id: \.self) { h in
                        Text(h).font(.caption2).foregroundStyle(.tertiary)
                            .gridColumnAlignment(h == "Time" || h == "Action" ? .leading : .trailing)
                    }
                }
                ForEach(Array(trades.enumerated()), id: \.offset) { _, t in
                    let isBuy = (t.action ?? "").lowercased().contains("buy")
                    GridRow {
                        Text(fmtDateTime(t.timestamp))
                        BacktestActionBadge(action: t.action ?? "")
                        Text(t.shares.map { dartToStringAsFixed($0.double, 4) } ?? "—")
                        Text(fmtMoney(t.price?.double))
                        Text(t.total.map { fmtMoney(abs($0.double)) } ?? "—")
                            .foregroundStyle(isBuy ? DS.Palette.danger : DS.Palette.success)
                        Text(fmtMoney(t.cashAfter?.double))
                    }
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        }
    }
}

/// BUY / SELL tag (`_ActionBadge`).
struct BacktestActionBadge: View {
    let action: String

    var body: some View {
        let up = action.uppercased()
        let c: Color = action.lowercased().contains("buy") ? DS.Palette.success : DS.Palette.danger
        MarketsTag(text: up, color: c)
    }
}

struct BacktestDecisionTrace: View {
    let decisions: [BacktestDecision]
    let limit: Int
    let onShowMore: () -> Void

    var body: some View {
        let visible = Array(decisions.prefix(limit))
        let remaining = decisions.count - visible.count
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                BacktestEyebrow(text: "Decision Trace")
                Spacer()
                Text("\(decisions.count) evaluations").font(.caption2).foregroundStyle(.secondary)
            }
            ForEach(Array(visible.enumerated()), id: \.offset) { _, d in card(d) }
            if remaining > 0 {
                Button(action: onShowMore) {
                    Text("Show more (\(remaining) remaining)")
                        .font(.footnote)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 6)
                }
                .buttonStyle(.bordered)
                .tint(.secondary)
            }
        }
        .padding(12)
    }

    private func color(_ label: String) -> Color {
        label == "BUY" ? DS.Palette.success : (label == "SELL" ? DS.Palette.danger : .secondary)
    }

    private func card(_ d: BacktestDecision) -> some View {
        let label = d.decisionLabel()
        return VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(fmtDateTime(d.timestamp)).font(.caption2).foregroundStyle(.secondary)
                    MarketsFlowLayout(spacing: 6, runSpacing: 4) {
                        AppBadge(label: label, color: color(label))
                        if d.overrideApplied == true { AppBadge(label: "Override", color: DS.Palette.warning) }
                        if let primary = d.primaryStrategy { MarketsChip(text: primary, color: .secondary) }
                    }
                }
                Spacer()
                if let score = d.normalizedScore {
                    VStack(alignment: .trailing, spacing: 0) {
                        Text("Weighted Score").font(.caption2).foregroundStyle(.tertiary)
                        Text(dartToStringAsFixed(score.double, 3)).font(.footnote.monospaced()).foregroundStyle(.secondary)
                    }
                }
            }
            if let reason = d.finalReason, !reason.isEmpty {
                Text(reason).font(.subheadline)
            }
            ForEach(Array(d.strategies.enumerated()), id: \.offset) { _, s in
                HStack(alignment: .top, spacing: 6) {
                    AppBadge(label: s.decisionLabel(), color: color(s.decisionLabel()))
                    VStack(alignment: .leading, spacing: 0) {
                        Text(s.strategy ?? "?").font(.subheadline.weight(.semibold))
                        if let r = s.reason {
                            Text(BacktestDetailFormat.truncateReason(r)).font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
    }
}

// MARK: - Round trips

struct BacktestRoundTripStats: View {
    let summary: BacktestSummary

    var body: some View {
        Card(padding: 16) {
            VStack(alignment: .leading, spacing: 12) {
                BacktestEyebrow(text: "Round Trip Statistics")
                LazyVGrid(columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)], spacing: 10) {
                    StatTile(label: "Round Trips", value: BacktestDetailFormat.numText(summary.roundTrips, "—"))
                    StatTile(label: "Total RT P&L", value: fmtPnl(summary.totalRoundTripPnl?.double), valueColor: pnlColor(summary.totalRoundTripPnl?.double))
                    StatTile(label: "Avg Winning", value: fmtMoney(summary.avgWinningRoundTrip?.double), valueColor: DS.Palette.success)
                    StatTile(label: "Avg Losing", value: fmtMoney(summary.avgLosingRoundTrip?.double), valueColor: DS.Palette.danger)
                }
            }
        }
    }
}

// MARK: - Skeleton

struct BacktestDetailSkeleton: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 12) {
                Skeleton.circle(44)
                VStack(alignment: .leading, spacing: 6) {
                    Skeleton(width: 160, height: 20, radius: 6)
                    Skeleton.line(width: 140, height: 11)
                    Skeleton.line(width: 100, height: 10)
                }
            }
            HStack(spacing: 8) {
                Skeleton(width: 64, height: 28, radius: 8)
                Skeleton(width: 64, height: 28, radius: 8)
                Skeleton(width: 80, height: 28, radius: 8)
            }
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                ForEach(0..<6, id: \.self) { _ in Skeleton(height: 56, radius: 8) }
            }
            Skeleton(height: 72, radius: DS.Radius.card)
            Skeleton(height: 200, radius: DS.Radius.card)
            ForEach(0..<3, id: \.self) { _ in Skeleton(height: 44, radius: 16) }
        }
        .accessibilityLabel("Loading")
    }
}
