import SwiftUI

// The sections of the backtest detail screen (backtest_detail_screen.dart's
// private widgets), each an inset-grouped list `Section`: title-style
// headers, `StatGrid`s and `LabeledContent` rows, no cards or grey tiles.

// MARK: - LLM pause banner

/// Shown only when the status is `paused_llm_critical` —
/// `BacktestLlmPauseBanner`: the pause details as rows, under a warning
/// headline.
struct BacktestLlmPauseBanner: View {
    let summary: BacktestSummary

    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        if (summary.status ?? "").lowercased() == "paused_llm_critical" {
            let ink = DS.Palette.onTint(DS.Palette.warning, in: colorScheme)
            Section {
                Label {
                    Text("Backtest paused: LLM critical failure")
                        .font(.headline)
                } icon: {
                    Image(systemName: Symbol.named("pause_circle_outline"))
                }
                .foregroundStyle(ink)
                LabeledContent("Bar", value: BacktestDetailFormat.fmtBar(summary.pauseBarTime))
                LabeledContent("Reason", value: "\(summary.pauseReasonTag ?? "unknown")  (\(BacktestDetailFormat.numText(summary.pauseAttempts, "?")) attempts)")
                LabeledContent("Provider", value: "\(summary.pauseProvider ?? "?")  •  Model: \(summary.pauseModel ?? "?")")
                LabeledContent("Call site", value: summary.pauseCallSite ?? "unknown")
                LabeledContent("Paused at", value: BacktestDetailFormat.fmtPausedAt(summary.pausedAt))
                if let sample = summary.pauseSample, !sample.isEmpty {
                    Text(sample)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
            } footer: {
                Text("Tap \"Resume\" above when the provider is healthy again. The same bar will retry from the snapshot.")
            }
        }
    }
}

// MARK: - Nexus lookback banner

struct BacktestNexusLookbackBanner: View {
    let lookback: NexusLookback

    var body: some View {
        Section {
            VStack(alignment: .leading, spacing: 6) {
                LabeledContent("Day \(lookback.current) / \(lookback.total)") {
                    Text(lookback.currentDate ?? "")
                        .monospacedDigit()
                }
                ProgressView(value: min(max(lookback.fraction, 0), 1))
                HStack {
                    Text(lookback.startDate ?? "")
                    Spacer()
                    Text(lookback.endDate ?? "")
                }
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
            }
            .padding(.vertical, 4)
        } header: {
            Text("Nexus Lookback Training")
        } footer: {
            Text("Building historical event context before trading begins. This runs once per scope.")
        }
    }
}

// MARK: - Stat grid

/// The run's figures in a Stocks-style `StatGrid` (the total P&L is the
/// screen's hero).
struct BacktestStatGrid: View {
    let summary: BacktestSummary
    let elapsed: Num?

    var body: some View {
        let s = summary
        let winRate = s.winRatePercent
        Section("Results") {
            StatGrid(columns: 2) {
                StatCell(label: "P&L %", value: fmtPct(s.pnlPercent?.double), valueColor: pnlColor(s.pnlPercent?.double))
                StatCell(
                    label: "Portfolio",
                    value: fmtMoney(s.portfolioEndValue?.double),
                    valueColor: pnlColor((s.portfolioEndValue?.double ?? 0) - (s.portfolioStartValue?.double ?? 0)),
                    footnote: "From \(fmtMoney(s.portfolioStartValue?.double))"
                )
                StatCell(
                    label: "Trades",
                    value: BacktestDetailFormat.numText(s.totalTrades, "—"),
                    footnote: "\(BacktestDetailFormat.numText(s.totalBuys, "0")) buy / \(BacktestDetailFormat.numText(s.totalSells, "0")) sell"
                )
                StatCell(
                    label: "Win rate",
                    value: winRate.map { "\(dartToStringAsFixed($0.double, 1))%" } ?? "—",
                    footnote: "\(BacktestDetailFormat.numText(s.winningRoundTrips, "0"))W / \(BacktestDetailFormat.numText(s.losingRoundTrips, "0"))L"
                )
                StatCell(
                    label: "Portfolio high",
                    value: fmtMoney(s.portfolioValueHigh?.double),
                    footnote: "Low: \(fmtMoney(s.portfolioValueLow?.double))"
                )
                StatCell(label: "Elapsed", value: fmtElapsed(elapsed?.double))
            }
            .padding(.vertical, 4)
        }
    }
}

// MARK: - AI credits

/// "AI credits": the totals as a `StatGrid`, then by model, call site and
/// provider as rows. It refreshes with the screen's pull-to-refresh.
struct BacktestLlmCostCard: View {
    let llmCost: LlmCost?
    let loading: Bool
    let error: String?

    var body: some View {
        Section {
            if let error {
                Text(error).foregroundStyle(DS.Palette.danger)
            } else if llmCost == nil, loading {
                totalsGrid(LlmCost(json: .null))
                    .redacted(reason: .placeholder)
            } else if let cost = llmCost, (cost.totalCalls?.double ?? 0) != 0 {
                totalsGrid(cost)
            } else {
                Text("No LLM calls were attributed to this backtest.")
                    .foregroundStyle(.secondary)
            }
        } header: {
            HStack(spacing: 8) {
                Text("AI credits")
                if loading { ProgressView().controlSize(.mini) }
            }
        } footer: {
            Text("LLM cost for this backtest")
        }
        if error == nil, let cost = llmCost, (cost.totalCalls?.double ?? 0) != 0 {
            group("By model", cost.byModel)
            group("By call site", cost.byCallSite)
            group("By provider", cost.byProvider)
        }
    }

    private func totalsGrid(_ c: LlmCost) -> some View {
        StatGrid(columns: 2) {
            StatCell(label: "Total cost", value: fmtUsdCost(c.totalCostUsd?.double))
            StatCell(
                label: "Calls",
                value: BacktestDetailFormat.numText(c.totalCalls, "0"),
                footnote: "\(BacktestDetailFormat.numText(c.okCalls, "0")) ok · \(BacktestDetailFormat.numText(c.failedCalls, "0")) failed"
            )
            StatCell(label: "Input tokens", value: tokens(c.totalInputTokens))
            StatCell(label: "Output tokens", value: tokens(c.totalOutputTokens))
        }
        .padding(.vertical, 4)
    }

    private func tokens(_ n: Num?) -> String {
        switch n {
        case .int(let i): fmtTokens(i)
        case .double(let d): fmtTokens(d)
        case nil: fmtTokens(Int?.none)
        }
    }

    @ViewBuilder
    private func group(_ title: String, _ rows: [LlmCostRow]) -> some View {
        if !rows.isEmpty {
            Section(title) {
                ForEach(Array(rows.prefix(6).enumerated()), id: \.offset) { _, r in
                    LabeledContent {
                        Text(fmtUsdCost(r.costUsd?.double)).monospacedDigit()
                    } label: {
                        Text(r.key).lineLimit(1).truncationMode(.middle)
                    }
                }
            }
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
            Section {
                LabeledContent("Crypto volume traded") {
                    Text(fmtMoney(volume)).monospacedDigit()
                }
                ForEach(BacktestDetailFormat.feePlatforms, id: \.id) { p in
                    let applied = abs(p.rate - appliedRate) < 1e-9
                    HStack(spacing: 10) {
                        HStack(spacing: 6) {
                            Text(p.name).lineLimit(1)
                            if applied { MarketsTag(text: "Applied") }
                        }
                        Spacer()
                        Text("\(dartToStringAsFixed(p.rate * 100, 2))%")
                            .font(.footnote.monospacedDigit())
                            .foregroundStyle(.secondary)
                        Text(fmtMoney(applied ? (actual > 0 ? actual : volume * p.rate) : volume * p.rate))
                            .monospacedDigit()
                            .foregroundStyle(applied ? Color.primary : Color.secondary)
                            .frame(minWidth: 84, alignment: .trailing)
                    }
                }
            } header: {
                HStack(spacing: 8) {
                    Text("Fees · crypto")
                    if emulated {
                        MarketsTag(text: "Emulated · \(appliedLabel)", color: DS.Palette.warning)
                    }
                }
            } footer: {
                Text(emulated
                     ? "\(appliedLabel) fees were emulated here (not your instance's brokerage) — the \"applied\" row. Other venues are estimates (volume × rate); actual tiers vary."
                     : "\(appliedLabel) is the fee actually charged in this backtest; other venues are estimates at their listed taker rate (volume × rate) — actual tiers vary.")
            }
        }
    }
}

// MARK: - Strategy

/// The run's strategy as a disclosure: its name, then each sub-strategy and
/// its raw config (monospaced, as code-like keys are).
struct BacktestStrategySection: View {
    let schema: StrategySchema
    let strategyId: String?
    @Binding var open: Bool

    var body: some View {
        Section("Strategy") {
            DisclosureGroup(isExpanded: $open) {
                if let strategyId {
                    LabeledContent("ID", value: strategyId)
                }
                ForEach(Array(schema.strategies.enumerated()), id: \.offset) { i, sub in
                    subStrategy(i, sub)
                }
                if schema.strategies.isEmpty {
                    Text("No sub-strategies defined").foregroundStyle(.secondary)
                }
            } label: {
                Text(schema.name ?? "—")
                    .font(.headline)
            }
        }
    }

    private func subStrategy(_ index: Int, _ sub: BacktestSubStrategy) -> some View {
        // `{...conditions, ...config}`: config overrides a shared key in place.
        var combined = sub.conditions.entries.map { ($0.key, $0.value) }
        for e in sub.config.entries {
            if let i = combined.firstIndex(where: { $0.0 == e.key }) { combined[i].1 = e.value } else { combined.append((e.key, e.value)) }
        }
        let meta = [sub.weight.map { "\(dartToStringAsFixed($0.double * 100, 0))%" }, sub.decisionPhase].compactMap { $0 }
        return VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("\(index + 1)")
                    .font(.headline.monospacedDigit())
                    .foregroundStyle(.secondary)
                Text(sub.strategy ?? "?").font(.headline)
                Spacer()
                if !meta.isEmpty {
                    Text(meta.joined(separator: " · "))
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            }
            if !combined.isEmpty {
                Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 4) {
                    ForEach(Array(combined.enumerated()), id: \.offset) { _, kv in
                        GridRow {
                            Text(kv.0)
                                .font(.caption.monospaced())
                                .foregroundStyle(.secondary)
                            Text(kv.1.dartDescription)
                                .font(.caption.monospaced())
                                .lineLimit(2)
                        }
                    }
                }
            }
        }
        .padding(.vertical, 4)
    }
}

// MARK: - Logs

/// "Logs": one row that opens the log viewer.
struct BacktestLogsSection: View {
    let id: String
    let model: BacktestDetailModel

    var body: some View {
        Section("Logs") {
            NavigationLink {
                BacktestLogsScreen(id: id, model: model)
            } label: {
                LabeledContent {
                    if !model.logLines.isEmpty {
                        Text("\(model.logLines.count) lines")
                    }
                } label: {
                    Label("View Logs", systemImage: Symbol.named("terminal"))
                }
            }
        }
    }
}

/// The backtest's log, pushed from its row — the Dart `_LogsPanel` body.
/// The first open loads it (`GET /backtests/:id/logs`), as the panel did.
struct BacktestLogsScreen: View {
    let id: String
    let model: BacktestDetailModel

    var body: some View {
        Group {
            if model.logsLoading {
                LoadingState(label: "Loading logs…")
                    .frame(maxHeight: .infinity)
            } else if let error = model.logsError {
                ContentUnavailableView {
                    Label(error, systemImage: Symbol.named("error_outline"))
                }
            } else if model.logLines.isEmpty {
                ContentUnavailableView("No logs available.", systemImage: Symbol.named("terminal"))
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 2) {
                        ForEach(Array(model.logLines.enumerated()), id: \.offset) { _, line in
                            Text(line)
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(BacktestDetailFormat.levelColor(line))
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    .padding(16)
                    .textSelection(.enabled)
                }
                .defaultScrollAnchor(.bottom)
            }
        }
        .background(DS.Surface.panel)
        .navigationTitle("backtest-\(id).log")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if model.logSource == "db" || !model.logLines.isEmpty {
                ToolbarItem(placement: .status) {
                    HStack(spacing: 6) {
                        if !model.logLines.isEmpty {
                            Text("\(model.logLines.count) lines")
                        }
                        if model.logSource == "db" {
                            Text("(last 500)").foregroundStyle(DS.Palette.warning)
                        }
                    }
                    .font(.footnote)
                }
            }
        }
        .task {
            if model.logLines.isEmpty, !model.logsLoading { await model.loadLogs() }
        }
    }
}

// MARK: - Portfolio chart

/// The portfolio value over time, under the hero: a readout of the value
/// (scrubbed or latest) against the start, then the chart.
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
        VStack(alignment: .leading, spacing: 4) {
            Text("Portfolio value over time")
                .font(.subheadline)
                .foregroundStyle(.secondary)
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(fmtMoney(display))
                    .font(.headline.monospacedDigit())
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
                height: 220,
                baseline: startValue,
                onScrub: { scrubIdx = $0 }
            )
            .padding(.top, 4)
        }
    }
}

// MARK: - P&L per stock

struct BacktestPnlPerStockCard: View {
    let summary: BacktestSummary

    var body: some View {
        Section {
            ForEach(summary.tickers, id: \.self) { sym in
                let p = summary.pnlPerStock?[sym]?.double
                let pp = summary.pnlPercentPerStock?[sym]?.double
                let pc = summary.stockPriceChange?[sym]?.double
                HStack(spacing: 12) {
                    Text(sym).font(.headline.monospaced())
                    Spacer()
                    Text(fmtPnl(p)).foregroundStyle(pnlColor(p))
                    Text(fmtPct(pp)).font(.footnote).foregroundStyle(pnlColor(pp))
                    Text(fmtPct(pc)).font(.footnote).foregroundStyle(pnlColor(pc))
                }
                .monospacedDigit()
                .accessibilityElement(children: .combine)
            }
        } header: {
            Text("P&L per Stock")
        }
    }
}

// MARK: - Stock accordion

/// One ticker's row: its P&L and trade count; tap to expand its price chart
/// with buy / sell markers, the trade table and the decision trace.
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
                    VStack(alignment: .leading, spacing: 2) {
                        Text(sym).font(.headline.monospaced()).foregroundStyle(.primary)
                        Text("\(trades.count) trades").font(.subheadline).foregroundStyle(.secondary)
                    }
                    Spacer()
                    EntityRowValue(fmtPnl(pnl), color: pnlColor(pnl), detail: fmtPct(pnlPct), detailColor: pnlColor(pnlPct))
                    Image(systemName: "chevron.right")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(.tertiary)
                        .rotationEffect(.degrees(expanded ? 90 : 0))
                        .accessibilityHidden(true)
                }
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .accessibilityHint(expanded ? "Collapses the trades" : "Shows the trades")
            if expanded {
                expandedContent(trades)
            }
        }
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
            .padding(.vertical, 12)
        }
        if !trades.isEmpty {
            Divider()
            BacktestTradeTable(trades: trades)
        }
        if !decisions.isEmpty {
            Divider()
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
                        Text(h).font(.caption).foregroundStyle(.secondary)
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
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                }
            }
            .padding(.vertical, 12)
        }
    }
}

/// The Buy / Sell tag (`_ActionBadge`), sentence-cased.
struct BacktestActionBadge: View {
    let action: String

    var body: some View {
        let c: Color = action.lowercased().contains("buy") ? DS.Palette.success : DS.Palette.danger
        MarketsTag(text: action.lowercased().dsSentenceCased, color: c)
    }
}

struct BacktestDecisionTrace: View {
    let decisions: [BacktestDecision]
    let limit: Int
    let onShowMore: () -> Void

    var body: some View {
        let visible = Array(decisions.prefix(limit))
        let remaining = decisions.count - visible.count
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Decision Trace").font(.subheadline.weight(.semibold))
                Spacer()
                Text("\(decisions.count) evaluations").font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(Array(visible.enumerated()), id: \.offset) { i, d in
                if i > 0 { Divider() }
                entry(d)
            }
            if remaining > 0 {
                Button(action: onShowMore) {
                    Text("Show more (\(remaining) remaining)")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderless)
            }
        }
        .padding(.vertical, 12)
    }

    private func color(_ label: String) -> Color {
        label == "BUY" ? DS.Palette.success : (label == "SELL" ? DS.Palette.danger : .secondary)
    }

    private func entry(_ d: BacktestDecision) -> some View {
        let label = d.decisionLabel()
        return VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(fmtDateTime(d.timestamp)).font(.footnote).foregroundStyle(.secondary)
                    MarketsFlowLayout(spacing: 6, runSpacing: 4) {
                        AppBadge(label: label.lowercased(), color: color(label))
                        if d.overrideApplied == true { AppBadge(label: "Override", color: DS.Palette.warning) }
                        if let primary = d.primaryStrategy { MarketsChip(text: primary) }
                    }
                }
                Spacer()
                if let score = d.normalizedScore {
                    VStack(alignment: .trailing, spacing: 0) {
                        Text("Weighted Score").font(.caption).foregroundStyle(.secondary)
                        Text(dartToStringAsFixed(score.double, 3)).font(.footnote.monospacedDigit())
                    }
                }
            }
            if let reason = d.finalReason, !reason.isEmpty {
                Text(reason).font(.subheadline)
            }
            ForEach(Array(d.strategies.enumerated()), id: \.offset) { _, s in
                HStack(alignment: .top, spacing: 6) {
                    AppBadge(label: s.decisionLabel().lowercased(), color: color(s.decisionLabel()))
                    VStack(alignment: .leading, spacing: 0) {
                        Text(s.strategy ?? "?").font(.subheadline.weight(.semibold))
                        if let r = s.reason {
                            Text(BacktestDetailFormat.truncateReason(r)).font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
    }
}

// MARK: - Round trips

struct BacktestRoundTripStats: View {
    let summary: BacktestSummary

    var body: some View {
        Section("Round trip statistics") {
            StatGrid(columns: 2) {
                StatCell(label: "Round trips", value: BacktestDetailFormat.numText(summary.roundTrips, "—"))
                StatCell(label: "Total RT P&L", value: fmtPnl(summary.totalRoundTripPnl?.double), valueColor: pnlColor(summary.totalRoundTripPnl?.double))
                StatCell(label: "Avg winning", value: fmtMoney(summary.avgWinningRoundTrip?.double), valueColor: DS.Palette.success)
                StatCell(label: "Avg losing", value: fmtMoney(summary.avgLosingRoundTrip?.double), valueColor: DS.Palette.danger)
            }
            .padding(.vertical, 4)
        }
    }
}
