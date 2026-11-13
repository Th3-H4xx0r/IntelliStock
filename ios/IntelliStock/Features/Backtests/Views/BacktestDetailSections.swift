import SwiftUI

// The sections of the backtest detail screen, each a `DSSection` (or a card
// in a plain one) in the web's words and order (`BacktestDetailView.vue`).

// MARK: - LLM pause banner

/// Shown only when the status is `paused_llm_critical` — the web's
/// `BacktestLLMPauseBanner`: an amber dot and the headline, the pause
/// details as key-value lines, the provider's sample, and what to do.
struct BacktestLlmPauseBanner: View {
    let summary: BacktestSummary

    var body: some View {
        if (summary.status ?? "").lowercased() == "paused_llm_critical" {
            Section {
                Card {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack(spacing: 8) {
                            DSStatusDot(.warn)
                            Text("Backtest paused: LLM critical failure")
                                .font(DS.Font.bodyMedium)
                                .foregroundStyle(DS.Text.primary)
                                .accessibilityAddTraits(.isHeader)
                        }
                        Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 16, verticalSpacing: 6) {
                            line("Bar", Text(BacktestDetailFormat.fmtBar(summary.pauseBarTime)).monospacedDigit())
                            line("Reason", Text("\(summary.pauseReasonTag ?? "unknown") \(Text("· \(BacktestDetailFormat.numText(summary.pauseAttempts, "?")) attempts").foregroundStyle(DS.Text.muted))"))
                            line("Provider", Text("\(summary.pauseProvider ?? "?") \(Text("· model \(summary.pauseModel ?? "?")").foregroundStyle(DS.Text.muted))"))
                            line("Call site", Text(summary.pauseCallSite ?? "unknown"))
                            line("Paused at", Text(BacktestDetailFormat.fmtPausedAt(summary.pausedAt)).monospacedDigit())
                        }
                        .font(DS.Font.body)
                        if let sample = summary.pauseSample, !sample.isEmpty {
                            Text(sample)
                                .font(DS.Font.mono)
                                .foregroundStyle(DS.Text.secondary)
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.horizontal, 12)
                                .padding(.vertical, 8)
                                .dsCardSurface(fill: DS.Surface.page, radius: DS.Radius.control, border: DS.Line.divider)
                        }
                        Text("Resume the backtest when the provider is healthy again. The same bar will retry from the snapshot")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .dsClearRow()
            }
        }
    }

    private func line(_ key: String, _ value: Text) -> some View {
        GridRow {
            Text(key)
                .foregroundStyle(DS.Text.muted)
                .frame(width: 72, alignment: .leading)
            value
                .foregroundStyle(DS.Text.soft)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

// MARK: - Nexus lookback banner

/// "Nexus lookback training" (`ui-card px-5 py-4`): the day count, a bar,
/// the start, current and end dates, and what it is doing.
struct BacktestNexusLookbackBanner: View {
    let lookback: NexusLookback

    var body: some View {
        Section {
            Card(padding: EdgeInsets(top: 16, leading: 20, bottom: 16, trailing: 20)) {
                VStack(alignment: .leading, spacing: 0) {
                    HStack(spacing: 16) {
                        Text("Nexus lookback training")
                            .font(DS.Font.bodyMedium)
                            .foregroundStyle(DS.Text.primary)
                        Spacer(minLength: 8)
                        Text("Day \(lookback.current) of \(lookback.total)")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.secondary)
                            .monospacedDigit()
                    }
                    BacktestProgressBar(fraction: lookback.fraction)
                        .padding(.top, 12)
                    HStack {
                        Text(lookback.startDate ?? "")
                        Spacer()
                        Text(lookback.currentDate ?? "").foregroundStyle(DS.Text.label)
                        Spacer()
                        Text(lookback.endDate ?? "")
                    }
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
                    .padding(.top, 8)
                    Text("Building historical event context before trading begins. This runs once per scope")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 12)
                }
                .accessibilityElement(children: .combine)
            }
            .dsClearRow()
        }
    }
}

// MARK: - Results

/// The web's results card: "Total P&L" with the figure in gain or loss and
/// "+47.51% return" under it, then a divider and the run's figures —
/// portfolio, trades, elapsed, win rate and portfolio high.
struct BacktestResultsCard: View {
    let summary: BacktestSummary
    let elapsed: Num?

    var body: some View {
        let s = summary
        let pnl = s.pnl?.double
        let pct = s.pnlPercent?.double
        Section {
            Card(padding: 0) {
                VStack(alignment: .leading, spacing: 0) {
                    VStack(alignment: .leading, spacing: 0) {
                        Text("Total P&L")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                        Text(fmtPnl(pnl))
                            .dsValueHero()
                            .foregroundStyle(backtestPnlColor(pnl))
                            .lineLimit(1)
                            .minimumScaleFactor(0.6)
                            .padding(.top, 12)
                        HStack(spacing: 6) {
                            Text(fmtPct(pct)).foregroundStyle(backtestPnlColor(pct))
                            Text("return").foregroundStyle(DS.Text.muted)
                        }
                        .font(DS.Font.body)
                        .monospacedDigit()
                        .padding(.top, 12)
                    }
                    .padding(DS.cardPadding)
                    .accessibilityElement(children: .combine)
                    DSDivider()
                    StatGrid(columns: 2) {
                        StatCell(label: "Portfolio", value: fmtMoney(s.portfolioEndValue?.double), footnote: "from \(fmtMoney(s.portfolioStartValue?.double))")
                        StatCell(
                            label: "Trades",
                            value: BacktestDetailFormat.numText(s.totalTrades, "—"),
                            footnote: "\(BacktestDetailFormat.numText(s.totalBuys, "0")) buy · \(BacktestDetailFormat.numText(s.totalSells, "0")) sell"
                        )
                        StatCell(label: "Elapsed", value: fmtElapsed(elapsed?.double))
                        StatCell(
                            label: "Win rate",
                            value: s.winRatePercent.map { "\(dartToStringAsFixed($0.double, 1))%" } ?? "—",
                            footnote: "\(BacktestDetailFormat.numText(s.winningRoundTrips, "0"))W · \(BacktestDetailFormat.numText(s.losingRoundTrips, "0"))L"
                        )
                        StatCell(label: "Portfolio high", value: fmtMoney(s.portfolioValueHigh?.double), footnote: "Low \(fmtMoney(s.portfolioValueLow?.double))")
                    }
                    .statValueStyle(.medium)
                    .padding(DS.cardPadding)
                }
            }
            .dsClearRow()
        }
    }
}

// MARK: - LLM cost

/// "LLM cost for this backtest" / "AI credits": the totals, then the cost by
/// model, by call site and by provider, on one card. It refreshes with the
/// screen's pull-to-refresh.
struct BacktestLlmCostCard: View {
    let llmCost: LlmCost?
    let loading: Bool
    let error: String?

    var body: some View {
        DSSection("LLM cost for this backtest", subtitle: "AI credits") {
            if let error {
                Text(error)
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Palette.loss)
            } else if llmCost == nil, loading {
                StatGrid(columns: 2) {
                    ForEach(0..<4, id: \.self) { _ in
                        VStack(alignment: .leading, spacing: 12) {
                            Skeleton(width: 64, height: 12)
                            Skeleton(width: 80, height: 22)
                        }
                    }
                }
                .padding(.vertical, 12)
                .accessibilityHidden(true)
            } else if let cost = llmCost, (cost.totalCalls?.double ?? 0) != 0 {
                totalsGrid(cost)
                group("By model", "No models recorded", cost.byModel)
                group("By call site", "No call sites recorded", cost.byCallSite)
                group("By provider", "No providers recorded", cost.byProvider)
            } else {
                Text("No LLM calls were attributed to this backtest. (Either the strategy ran with no LLM stages enabled, or the backtest finished before the telemetry sink flushed.)")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.vertical, 8)
            }
        } accessory: {
            if loading {
                ProgressView()
                    .controlSize(.small)
                    .frame(width: 32, height: 32)
            }
        }
    }

    private func totalsGrid(_ c: LlmCost) -> some View {
        let failed = c.failedCalls?.double ?? 0
        return StatGrid(columns: 2) {
            StatCell(label: "Total cost") {
                Text(fmtUsdCost(c.totalCostUsd?.double)).font(DS.Font.figureLarge)
            }
            StatCell(label: "Calls") {
                VStack(alignment: .leading, spacing: 4) {
                    Text(BacktestDetailFormat.numText(c.totalCalls, "0"))
                    Group {
                        if failed > 0 {
                            Text("\(BacktestDetailFormat.numText(c.okCalls, "0")) ok · \(Text("\(BacktestDetailFormat.numText(c.failedCalls, "0")) failed").foregroundStyle(DS.Palette.amber))")
                        } else {
                            Text("\(BacktestDetailFormat.numText(c.okCalls, "0")) ok")
                        }
                    }
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                }
            }
            StatCell(label: "Input tokens", value: fmtTokens(c.totalInputTokens))
            StatCell(
                label: "Output tokens",
                value: fmtTokens(c.totalOutputTokens),
                footnote: (c.totalReasoningTokens?.double ?? 0) > 0 ? "incl. \(fmtTokens(c.totalReasoningTokens)) reasoning" : nil
            )
        }
        .statValueStyle(.figure)
        .padding(.vertical, 12)
    }

    private func group(_ title: String, _ empty: String, _ rows: [LlmCostRow]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .padding(.bottom, 4)
            if rows.isEmpty {
                Text(empty)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
            } else {
                ForEach(Array(rows.prefix(6).enumerated()), id: \.offset) { _, r in
                    HStack(spacing: 12) {
                        Text(r.key)
                            .foregroundStyle(DS.Text.label)
                            .lineLimit(1)
                            .truncationMode(.middle)
                        Spacer(minLength: 8)
                        Text(fmtUsdCost(r.costUsd?.double))
                            .foregroundStyle(DS.Text.primary)
                            .monospacedDigit()
                    }
                    .font(DS.Font.body)
                    .accessibilityElement(children: .combine)
                }
            }
        }
        .padding(.vertical, 8)
    }
}

// MARK: - Crypto fees

/// "Estimated fees per platform" (crypto backtests only): the volume traded,
/// then each venue's taker rate and fee, the applied one marked.
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
            DSSection("Estimated fees per platform", subtitle: "Crypto", rowMinHeight: 48) {
                DSKeyValueRow("Crypto volume traded", fmtMoney(volume))
                ForEach(BacktestDetailFormat.feePlatforms, id: \.id) { p in
                    let applied = abs(p.rate - appliedRate) < 1e-9
                    HStack(spacing: 16) {
                        HStack(spacing: 8) {
                            Text(p.name)
                                .foregroundStyle(applied ? DS.Text.primary : DS.Text.label)
                                .lineLimit(1)
                            if applied {
                                Text("Applied")
                                    .font(DS.Font.caption)
                                    .foregroundStyle(DS.Text.muted)
                            }
                        }
                        Spacer(minLength: 8)
                        Text("\(dartToStringAsFixed(p.rate * 100, 2))%")
                            .foregroundStyle(DS.Text.muted)
                            .frame(minWidth: 56, alignment: .trailing)
                        Text(fmtMoney(applied ? (actual > 0 ? actual : volume * p.rate) : volume * p.rate))
                            .foregroundStyle(applied ? DS.Text.primary : DS.Text.secondary)
                            .frame(minWidth: 84, alignment: .trailing)
                    }
                    .font(DS.Font.body)
                    .monospacedDigit()
                    .accessibilityElement(children: .combine)
                }
            } footer: {
                Text(emulated
                     ? "\(appliedLabel) fees were emulated here (not your instance's brokerage) — it's the “applied” row. Other venues are estimates at their listed taker rate (volume × rate) — actual tiers vary"
                     : "\(appliedLabel) is the fee actually charged in this backtest. Other venues are estimates at their listed taker rate (volume × rate) — actual tiers vary")
            } accessory: {
                if emulated {
                    HStack(spacing: 8) {
                        DSStatusDot(.warn)
                        Text("Emulated · \(appliedLabel)")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.secondary)
                    }
                }
            }
        }
    }
}

// MARK: - Strategy

/// The run's strategy (the web's collapsible card): its name over
/// "Strategy"; open it for the id and each sub-strategy with its weight,
/// position, phase and scope, and its config.
struct BacktestStrategySection: View {
    let schema: StrategySchema
    let strategyId: String?
    @Binding var open: Bool

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        DSSection {
            Button {
                withAnimation(DSMotion.standard(reduceMotion: reduceMotion)) { open.toggle() }
            } label: {
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(schema.name ?? "—")
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.primary)
                            .lineLimit(1)
                        Text("Strategy")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    Image(systemName: "chevron.down")
                        .font(.system(size: 14))
                        .foregroundStyle(DS.Text.muted)
                        .rotationEffect(.degrees(open ? 180 : 0))
                        .accessibilityHidden(true)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityHint(open ? "Hides the strategy" : "Shows the strategy")
            if open {
                details(schema)
            }
        }
    }

    private func details(_ schema: StrategySchema) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if let strategyId {
                Text("ID \(strategyId)")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
                    .padding(.bottom, 8)
            }
            Text("Sub-strategies")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
            VStack(spacing: 0) {
                if schema.strategies.isEmpty {
                    Text("No sub-strategies defined")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.muted)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 12)
                }
                ForEach(Array(schema.strategies.enumerated()), id: \.offset) { i, sub in
                    if i > 0 { DSDivider() }
                    subStrategy(i, sub)
                }
            }
            .dsCardSurface(fill: .clear, radius: DS.Radius.control, border: DS.Line.divider)
        }
        .padding(.vertical, 8)
    }

    private func subStrategy(_ index: Int, _ sub: BacktestSubStrategy) -> some View {
        // `{...conditions, ...config}`: config overrides a shared key in place.
        var combined = sub.conditions.entries.map { ($0.key, $0.value) }
        for e in sub.config.entries {
            if let i = combined.firstIndex(where: { $0.0 == e.key }) { combined[i].1 = e.value } else { combined.append((e.key, e.value)) }
        }
        let meta = [
            sub.weight.map { "\(dartToStringAsFixed($0.double * 100, 0))%" },
            sub.executionPosition.map { "pos \($0.description)" },
            sub.decisionPhase,
            sub.executionScope,
        ].compactMap { $0 }
        return VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text("\(index + 1)")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
                    .frame(width: 16, alignment: .leading)
                // The web wraps the weight, position, phase and scope under
                // the name when the line runs out.
                VStack(alignment: .leading, spacing: 2) {
                    Text(sub.strategy ?? "?")
                        .font(DS.Font.bodyMedium)
                        .foregroundStyle(DS.Text.primary)
                        .lineLimit(1)
                    if !meta.isEmpty {
                        Text(meta.joined(separator: " · "))
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.secondary)
                            .monospacedDigit()
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            if !combined.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Config")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                    LazyVGrid(columns: [GridItem(.flexible(), spacing: 16, alignment: .topLeading), GridItem(.flexible(), alignment: .topLeading)], alignment: .leading, spacing: 8) {
                        ForEach(Array(combined.enumerated()), id: \.offset) { _, kv in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(kv.0)
                                    .font(DS.Font.caption)
                                    .foregroundStyle(DS.Text.muted)
                                    .lineLimit(1)
                                Text(kv.1.isNull ? "null" : kv.1.dartDescription)
                                    .font(DS.Font.body)
                                    .foregroundStyle(DS.Text.soft)
                                    .monospacedDigit()
                                    .lineLimit(1)
                            }
                            .accessibilityElement(children: .combine)
                        }
                    }
                }
                .padding(.leading, 28)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }
}

// MARK: - Logs

/// The log's card row: "backtest-<id>.log" and its line count; it opens the
/// log viewer.
struct BacktestLogsSection: View {
    let id: String
    let model: BacktestDetailModel

    var body: some View {
        var meta: [String] = []
        if !model.logLines.isEmpty { meta.append("\(model.logLines.count) lines") }
        if model.logSource == "db" { meta.append("last 500 — no full log file") }
        return DSSection {
            NavigationLink {
                BacktestLogsScreen(id: id, model: model)
            } label: {
                EntityRow("backtest-\(id).log", subtitle: meta.isEmpty ? nil : meta.joined(separator: " · ")) {
                    Text("View logs")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.secondary)
                }
            }
        }
    }
}

/// The backtest's log, pushed from its row: monospaced lines on the page,
/// coloured by level as the web colours them. The first open loads it
/// (`GET /backtests/:id/logs`).
struct BacktestLogsScreen: View {
    let id: String
    let model: BacktestDetailModel

    var body: some View {
        Group {
            if model.logsLoading {
                VStack(alignment: .leading, spacing: 8) {
                    Skeleton(width: 220, height: 12)
                    Skeleton(width: 150, height: 12)
                    Skeleton(width: 180, height: 12)
                }
                .padding(20)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .accessibilityLabel("Loading logs")
            } else if let error = model.logsError {
                ErrorRow(message: error) { Task { await model.loadLogs() } }
                    .padding(DS.pageGutter)
                    .frame(maxHeight: .infinity, alignment: .top)
            } else if model.logLines.isEmpty {
                Card(padding: 0) {
                    EmptyState(title: "No logs available for this backtest")
                }
                .padding(DS.pageGutter)
                .frame(maxHeight: .infinity, alignment: .top)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 2) {
                        ForEach(Array(model.logLines.enumerated()), id: \.offset) { _, line in
                            Text(line)
                                .font(DS.Font.monoSmall)
                                .foregroundStyle(BacktestDetailFormat.levelColor(line))
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    .padding(DS.cardPadding)
                    .textSelection(.enabled)
                }
                .defaultScrollAnchor(.bottom)
            }
        }
        .background(DS.Surface.page)
        .navigationTitle("backtest-\(id).log")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if model.logSource == "db" || !model.logLines.isEmpty {
                ToolbarItem(placement: .status) {
                    HStack(spacing: 6) {
                        if !model.logLines.isEmpty {
                            Text("\(model.logLines.count) lines").monospacedDigit()
                        }
                        if model.logSource == "db" {
                            Text("last 500 — no full log file")
                        }
                    }
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                }
            }
        }
        .task {
            if model.logLines.isEmpty, !model.logsLoading { await model.loadLogs() }
        }
    }
}

// MARK: - Portfolio chart

/// "Portfolio value over time": the value (scrubbed or latest) as the
/// figure, its change against the start, then the chart with its axis.
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
        VStack(alignment: .leading, spacing: 0) {
            Text(fmtMoney(display))
                .dsValueHero()
                .foregroundStyle(DS.Text.primary)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
                .contentTransition(.numericText(value: display))
            HStack(spacing: 6) {
                Text(fmtPnl(pnl)).foregroundStyle(backtestPnlColor(pnl))
                Text("vs start").foregroundStyle(DS.Text.muted)
                Spacer(minLength: 8)
                Text(i.map { fmtDateTime(timestamps[$0]) } ?? " ")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
            }
            .font(DS.Font.body)
            .monospacedDigit()
            .padding(.top, 8)
            ScrubbableAreaChart(
                timestamps: timestamps,
                values: values,
                lineColor: isUp ? DS.Palette.gain : DS.Palette.loss,
                height: 260,
                baseline: startValue,
                onScrub: { scrubIdx = $0 },
                showsYAxis: true
            )
            .padding(.top, 24)
        }
    }
}

// MARK: - P&L per stock

/// "P&L per stock": a table card — stock, P&L, P&L % and price change.
struct BacktestPnlPerStockCard: View {
    let summary: BacktestSummary

    var body: some View {
        DSSection("P&L per stock", style: .plain) {
            Card(padding: 0) {
                DSTableHeader {
                    Text("Stock")
                    Spacer(minLength: 8)
                    Text("P&L").frame(width: Self.pnlWidth, alignment: .trailing)
                    Text("P&L %").frame(width: Self.pctWidth, alignment: .trailing)
                    Text("Price chg").frame(width: Self.pctWidth, alignment: .trailing)
                }
                DSRows {
                    ForEach(summary.tickers, id: \.self) { sym in
                        let p = summary.pnlPerStock?[sym]?.double
                        let pp = summary.pnlPercentPerStock?[sym]?.double
                        let pc = summary.stockPriceChange?[sym]?.double
                        DSRow {
                            HStack(spacing: 12) {
                                Text(sym)
                                    .font(DS.Font.bodyMedium)
                                    .foregroundStyle(DS.Text.primary)
                                    .lineLimit(1)
                                Spacer(minLength: 8)
                                Text(fmtPnl(p))
                                    .foregroundStyle(backtestPnlColor(p))
                                    .frame(width: Self.pnlWidth, alignment: .trailing)
                                Text(fmtPct(pp))
                                    .foregroundStyle(backtestPnlColor(pp))
                                    .frame(width: Self.pctWidth, alignment: .trailing)
                                Text(fmtPct(pc))
                                    .foregroundStyle(DS.Text.secondary)
                                    .frame(width: Self.pctWidth, alignment: .trailing)
                            }
                            .font(DS.Font.body)
                            .monospacedDigit()
                            .lineLimit(1)
                            .minimumScaleFactor(0.8)
                            .accessibilityElement(children: .combine)
                        }
                    }
                }
            }
            .dsClearRow()
        }
    }

    private static let pnlWidth: CGFloat = 88
    private static let pctWidth: CGFloat = 64
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
                HStack(spacing: 12) {
                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        Text(sym)
                            .font(DS.Font.bodyMedium)
                            .foregroundStyle(DS.Text.primary)
                        Text("\(fmtPnl(pnl)) (\(fmtPct(pnlPct)))")
                            .font(DS.Font.body)
                            .foregroundStyle(backtestPnlColor(pnl))
                            .lineLimit(1)
                        Text("\(trades.count) trades")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                            .lineLimit(1)
                    }
                    .monospacedDigit()
                    .frame(maxWidth: .infinity, alignment: .leading)
                    Image(systemName: "chevron.down")
                        .font(.system(size: 14))
                        .foregroundStyle(DS.Text.muted)
                        .rotationEffect(.degrees(expanded ? 180 : 0))
                        .accessibilityHidden(true)
                }
                .frame(minHeight: 40)
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
        VStack(alignment: .leading, spacing: 0) {
            DSDivider()
            if prices.isEmpty {
                Text("No price data for \(sym)")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
                    .padding(.vertical, 16)
            } else {
                let buys = trades.filter { ($0.action ?? "").lowercased().contains("buy") }
                let sells = trades.filter { ($0.action ?? "").lowercased().contains("sell") }
                let markers = buys.map { ScrubbableChartMarker(date: $0.timestamp ?? Date(), value: $0.price?.double ?? 0, color: DS.Palette.gain) }
                    + sells.map { ScrubbableChartMarker(date: $0.timestamp ?? Date(), value: $0.price?.double ?? 0, color: DS.Palette.loss) }
                HStack(spacing: 12) {
                    BacktestDotWord(word: "Buy", color: DS.Palette.gain)
                    BacktestDotWord(word: "Sell", color: DS.Palette.loss)
                }
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .padding(.top, 16)
                ScrubbableAreaChart(
                    timestamps: prices.map(\.timestamp),
                    values: prices.map(\.close),
                    lineColor: DS.Chart.lead,
                    height: 220,
                    markers: markers,
                    showsYAxis: true
                )
                .padding(.vertical, 12)
            }
            if !trades.isEmpty {
                DSDivider()
                BacktestTradeTable(trades: trades)
            }
            if !decisions.isEmpty {
                DSDivider()
                BacktestDecisionTrace(decisions: decisions, limit: decisionLimit, onShowMore: onShowMoreDecisions)
            }
        }
    }
}

/// A 6 pt dot and a word (the web's buy / sell / decision markers).
struct BacktestDotWord: View {
    let word: String
    let color: Color

    var body: some View {
        HStack(spacing: 6) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text(word)
        }
        .accessibilityElement(children: .combine)
    }
}

/// The trade table (Time / Action / Shares / Price / Total / Cash after),
/// scrolling sideways as the web's table does on a phone.
struct BacktestTradeTable: View {
    let trades: [BacktestTrade]

    private static let headers = ["Time", "Action", "Shares", "Price", "Total", "Cash after"]

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            Grid(alignment: .leading, horizontalSpacing: 20, verticalSpacing: 0) {
                GridRow {
                    ForEach(Self.headers, id: \.self) { h in
                        Text(h)
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                            .frame(height: 40)
                            .gridColumnAlignment(h == "Time" || h == "Action" ? .leading : .trailing)
                    }
                }
                Rectangle().fill(DS.Line.divider).frame(height: 1).gridCellUnsizedAxes(.horizontal)
                ForEach(Array(trades.enumerated()), id: \.offset) { i, t in
                    let isBuy = (t.action ?? "").lowercased().contains("buy")
                    if i > 0 {
                        Rectangle().fill(DS.Line.row).frame(height: 1).gridCellUnsizedAxes(.horizontal)
                    }
                    GridRow {
                        Text(fmtDateTime(t.timestamp)).foregroundStyle(DS.Text.secondary).frame(height: 48)
                        BacktestActionBadge(action: t.action ?? "")
                        Text(t.shares.map { dartToStringAsFixed($0.double, 4) } ?? "—").foregroundStyle(DS.Text.label)
                        Text(fmtMoney(t.price?.double)).foregroundStyle(DS.Text.label)
                        Text(t.total.map { fmtMoney(abs($0.double)) } ?? "—")
                            .foregroundStyle(isBuy ? DS.Palette.loss : DS.Palette.gain)
                        Text(fmtMoney(t.cashAfter?.double)).foregroundStyle(DS.Text.secondary)
                    }
                    .font(DS.Font.body)
                    .monospacedDigit()
                    .lineLimit(1)
                }
            }
            .padding(.bottom, 4)
        }
    }
}

/// The Buy / Sell marker: a dot and the word, sentence-cased (no pill).
struct BacktestActionBadge: View {
    let action: String

    var body: some View {
        let c: Color = action.lowercased().contains("buy") ? DS.Palette.gain : DS.Palette.loss
        BacktestDotWord(word: action.lowercased().dsSentenceCased, color: c)
            .foregroundStyle(DS.Text.soft)
    }
}

/// "Decision trace": each evaluation, newest first, as a bordered block —
/// its time, the decision as a dot and a word with any override and the
/// lead strategy, the weighted score, the reason and each strategy's vote —
/// then "Show 5 more".
struct BacktestDecisionTrace: View {
    let decisions: [BacktestDecision]
    let limit: Int
    let onShowMore: () -> Void

    var body: some View {
        let visible = Array(decisions.prefix(limit))
        let remaining = decisions.count - visible.count
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("Decision trace")
                    .font(DS.Font.bodyMedium)
                    .foregroundStyle(DS.Text.primary)
                Spacer()
                Text("\(decisions.count) evaluations")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
            }
            ForEach(Array(visible.enumerated()), id: \.offset) { _, d in
                entry(d)
            }
            if remaining > 0 {
                Button(action: onShowMore) {
                    HStack(spacing: 4) {
                        Text("Show \(min(BacktestDetailView.decisionPage, remaining)) more")
                            .foregroundStyle(DS.Text.secondary)
                        Text("· \(remaining) remaining")
                            .foregroundStyle(DS.Text.muted)
                    }
                    .font(DS.Font.body)
                    .monospacedDigit()
                    .frame(maxWidth: .infinity, minHeight: 40)
                    .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .dsCardSurface(fill: .clear, radius: DS.Radius.control, border: DS.Line.divider)
            }
        }
        .padding(.vertical, 16)
    }

    private func color(_ label: String) -> Color {
        label == "BUY" ? DS.Palette.gain : (label == "SELL" ? DS.Palette.loss : DS.Text.faint)
    }

    private func entry(_ d: BacktestDecision) -> some View {
        let label = d.decisionLabel()
        return VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 16) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(fmtDateTime(d.timestamp))
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                    FlowLayout(spacing: 8, runSpacing: 4) {
                        BacktestDotWord(word: label.lowercased().dsSentenceCased, color: color(label))
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.primary)
                        if d.overrideApplied == true {
                            Text("Override").font(DS.Font.caption).foregroundStyle(DS.Palette.amber)
                        }
                        if let primary = d.primaryStrategy {
                            Text(primary).font(DS.Font.caption).foregroundStyle(DS.Text.secondary)
                        }
                        if let intent = d.primaryActionIntent {
                            Text(intent).font(DS.Font.caption).foregroundStyle(DS.Text.muted)
                        }
                    }
                }
                Spacer(minLength: 0)
                VStack(alignment: .trailing, spacing: 2) {
                    Text("Weighted score")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                    Text(d.normalizedScore.map { dartToStringAsFixed($0.double, 3) } ?? "—")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.soft)
                        .monospacedDigit()
                }
            }
            if d.overrideApplied == true, let pre = d.preOverrideAction, !pre.isEmpty {
                Text("Final action changed from \(pre.uppercased()) to \(label).")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.secondary)
            }
            if let reason = d.finalReason, !reason.isEmpty {
                Text(reason)
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.soft)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !d.postDecision.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Post-decision output")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                    ForEach(Array(d.postDecision.enumerated()), id: \.offset) { _, post in
                        let name = post.strategy ?? "post_decision"
                        let verdict = backtestDecisionLabel(post.decision)
                        let reason = post.reason.map { " · \(BacktestDetailFormat.truncateReason($0))" } ?? ""
                        Text("\(name) \(Text("→").foregroundStyle(DS.Text.faint)) \(verdict)\(Text(reason).foregroundStyle(DS.Text.secondary))")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.soft)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .dsCardSurface(fill: .clear, radius: DS.Radius.control, border: DS.Line.divider)
            }
            ForEach(Array(d.strategies.enumerated()), id: \.offset) { _, s in
                let sLabel = s.decisionLabel()
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        Text(s.strategy ?? "strategy")
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.soft)
                            .lineLimit(1)
                        Spacer(minLength: 8)
                        BacktestDotWord(word: sLabel.lowercased().dsSentenceCased, color: color(sLabel))
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.label)
                    }
                    Text(["Weight \(s.weight.map { "\(dartToStringAsFixed($0.double * 100, 0))%" } ?? "—")", s.actionIntent].compactMap { $0 }.joined(separator: " · "))
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                    if let r = s.reason, !r.isEmpty {
                        Text(BacktestDetailFormat.truncateReason(r))
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .dsCardSurface(radius: DS.Radius.control, border: DS.Line.divider)
            }
        }
        .padding(16)
        .dsCardSurface(fill: DS.Surface.page, radius: DS.Radius.control, border: DS.Line.divider)
    }
}

// MARK: - Round trips

/// "Round trip statistics": four figures on one card.
struct BacktestRoundTripStats: View {
    let summary: BacktestSummary

    var body: some View {
        DSSection("Round trip statistics", style: .plain) {
            Card {
                StatGrid(columns: 2) {
                    StatCell(label: "Round trips", value: BacktestDetailFormat.numText(summary.roundTrips, "—"))
                    StatCell(label: "Total round trip P&L", value: fmtPnl(summary.totalRoundTripPnl?.double), valueColor: backtestPnlColor(summary.totalRoundTripPnl?.double))
                    StatCell(label: "Avg winning", value: fmtMoney(summary.avgWinningRoundTrip?.double), valueColor: DS.Palette.gain)
                    StatCell(label: "Avg losing", value: fmtMoney(summary.avgLosingRoundTrip?.double), valueColor: DS.Palette.loss)
                }
                .statValueStyle(.figure)
            }
            .dsClearRow()
        }
    }
}

// MARK: - TR scorecard

/// The TR scorecard's labels and number formats, shared with the web page
/// (frontend/src/utils/trScorecard.js): one decimal, a dash for null.
nonisolated enum TrScorecardFormat {
    struct PeriodRow: Hashable, Identifiable {
        let id: String
        let label: String
        let period: TrScorecardPeriod
    }

    static let title = "vs S&P 500 by market type"

    /// Whole test, then honest, then optimistic.
    static func periods(_ card: TrScorecard) -> [PeriodRow] {
        [
            PeriodRow(id: "overall", label: "Whole test", period: card.overall),
            PeriodRow(id: "honest", label: "Honest period (after AI training cutoff)", period: card.honest),
            PeriodRow(id: "optimistic", label: "Optimistic period (AI may remember)", period: card.optimistic),
        ]
    }

    static func errorNote(_ error: String) -> String { "Scorecard unavailable: \(error)" }

    static func verdictLabel(_ v: TrVerdict) -> String {
        switch v {
        case .pass: "Pass"
        case .fail: "Fail"
        case .untested: "Not tested yet"
        }
    }

    /// The web's `TR_TONE`: pass gain, fail loss, untested zinc-500.
    static func verdictColor(_ v: TrVerdict) -> Color {
        switch v {
        case .pass: DS.Palette.gain
        case .fail: DS.Palette.loss
        case .untested: DS.Text.muted
        }
    }

    /// The web's `toneOf`: gain above zero, loss below, zinc-500 for zero or
    /// no figure.
    static func tone(_ v: Double?) -> Color {
        guard let v, v.isFinite, v != 0 else { return DS.Text.muted }
        return v > 0 ? DS.Palette.gain : DS.Palette.loss
    }

    /// A window's "Beat?" colour.
    static func beatColor(_ beat: Bool?) -> Color {
        switch beat {
        case true?: DS.Palette.gain
        case false?: DS.Palette.loss
        case nil: DS.Text.muted
        }
    }

    /// "+12.3%" / "-4.0%" / "0.0%".
    static func signedPct(_ v: Double?) -> String {
        guard let v, v.isFinite else { return "—" }
        return "\(v > 0 ? "+" : "")\(dartToStringAsFixed(v, 1))%"
    }

    /// "-12.3%": drawdowns and percentiles, no forced sign.
    static func pct(_ v: Double?) -> String {
        guard let v, v.isFinite else { return "—" }
        return "\(dartToStringAsFixed(v, 1))%"
    }

    /// Percentage points a year: "+4.8 pts".
    static func points(_ v: Double?) -> String {
        guard let v, v.isFinite else { return "—" }
        return "\(v > 0 ? "+" : "")\(dartToStringAsFixed(v, 1)) pts"
    }

    static func marketType(_ kind: String) -> String {
        switch kind {
        case "bull": "Bull"
        case "chop": "Choppy"
        case "bear": "Bear"
        default: kind
        }
    }

    static func beatLabel(_ beat: Bool?) -> String {
        switch beat {
        case true?: "Yes"
        case false?: "No"
        case nil: "—"
        }
    }

    static func dates(_ start: String?, _ end: String?) -> String? {
        guard let start, let end else { return nil }
        return "\(start) – \(end)"
    }

    static func randomLines(candidates: Double?, universe: Double?) -> [String] {
        var lines: [String] = []
        if let c = candidates, c.isFinite {
            lines.append("Better than \(pct(c)) of random picks from the AI's own shortlist")
        }
        if let u = universe, u.isFinite {
            lines.append("Better than \(pct(u)) of random picks from all names it considered")
        }
        return lines
    }
}

/// "vs S&P 500 by market type" with the AI training cutoff under it: each
/// period's return a year, drawdown and pass flags against the S&P, then
/// each tested period's market-type windows and the notes, on one card.
/// Shown only when the summary carries a TR scorecard.
struct BacktestTrScorecardSection: View {
    let card: TrScorecard

    var body: some View {
        DSSection(TrScorecardFormat.title, subtitle: card.cutoff.map { "AI training cutoff: \($0)" }) {
            if let error = card.error {
                HStack(spacing: 8) {
                    DSStatusDot(.warn)
                    Text(TrScorecardFormat.errorNote(error))
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.secondary)
                }
            } else {
                let rows = TrScorecardFormat.periods(card)
                ForEach(rows) { periodSummary($0) }
                ForEach(rows.filter { !$0.period.untested }) { windows($0) }
                if !card.notes.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(Array(card.notes.enumerated()), id: \.offset) { _, note in
                            Text(note)
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Text.muted)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .padding(.vertical, 4)
                }
            }
        }
    }

    private func periodSummary(_ row: TrScorecardFormat.PeriodRow) -> some View {
        let p = row.period
        return VStack(alignment: .leading, spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(row.label)
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.primary)
                if let dates = TrScorecardFormat.dates(p.start, p.end) {
                    Text(dates)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                }
            }
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 4) {
                figure("Return/yr", TrScorecardFormat.signedPct(p.cagr), color: DS.Text.soft)
                figure("S&P/yr", TrScorecardFormat.signedPct(p.spyCagr), color: DS.Text.secondary)
                figure("Difference", TrScorecardFormat.points(p.excessPp), color: TrScorecardFormat.tone(p.excessPp))
                figure("Worst drop", TrScorecardFormat.pct(p.maxDd), color: DS.Text.soft)
                figure("S&P worst drop", TrScorecardFormat.pct(p.spyMaxDd), color: DS.Text.secondary)
                figure("Beat S&P in all market types", TrScorecardFormat.verdictLabel(p.passAllRegimes), color: TrScorecardFormat.verdictColor(p.passAllRegimes))
                figure("+5 pts/yr target", TrScorecardFormat.verdictLabel(p.passPlus5), color: TrScorecardFormat.verdictColor(p.passPlus5))
            }
            .font(DS.Font.body)
        }
        .padding(.vertical, 8)
        .accessibilityElement(children: .combine)
    }

    private func figure(_ label: String, _ value: String, color: Color) -> some View {
        GridRow {
            Text(label)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
            Text(value)
                .monospacedDigit()
                .foregroundStyle(color)
                .gridColumnAlignment(.trailing)
                .frame(maxWidth: .infinity, alignment: .trailing)
        }
    }

    private static let windowHeaders = ["Market type", "Dates", "Strategy", "S&P", "Beat?"]

    private func windows(_ row: TrScorecardFormat.PeriodRow) -> some View {
        let p = row.period
        let random = TrScorecardFormat.randomLines(candidates: p.randomPctileCandidates, universe: p.randomPctileUniverse)
        return VStack(alignment: .leading, spacing: 4) {
            Text(row.label)
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.primary)
            ForEach(random, id: \.self) { line in
                Text(line)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.secondary)
                    .monospacedDigit()
            }
            if p.windows.isEmpty {
                Text("No market-type windows fall inside this period")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 0) {
                        GridRow {
                            ForEach(Self.windowHeaders, id: \.self) { h in
                                Text(h)
                                    .font(DS.Font.caption)
                                    .foregroundStyle(DS.Text.muted)
                                    .frame(height: 36)
                                    .gridColumnAlignment(h == "Market type" || h == "Dates" ? .leading : .trailing)
                            }
                        }
                        Rectangle().fill(DS.Line.divider).frame(height: 1).gridCellUnsizedAxes(.horizontal)
                        ForEach(Array(p.windows.enumerated()), id: \.offset) { i, w in
                            if i > 0 {
                                Rectangle().fill(DS.Line.row).frame(height: 1).gridCellUnsizedAxes(.horizontal)
                            }
                            GridRow {
                                Text(TrScorecardFormat.marketType(w.kind)).foregroundStyle(DS.Text.soft).frame(height: 40)
                                Text(TrScorecardFormat.dates(w.start, w.end) ?? "—").foregroundStyle(DS.Text.secondary)
                                Text(TrScorecardFormat.signedPct(w.ret)).foregroundStyle(DS.Text.soft)
                                Text(TrScorecardFormat.signedPct(w.spyRet)).foregroundStyle(DS.Text.secondary)
                                Text(TrScorecardFormat.beatLabel(w.beat)).foregroundStyle(TrScorecardFormat.beatColor(w.beat))
                            }
                            .font(DS.Font.body)
                            .monospacedDigit()
                        }
                    }
                    .padding(.horizontal, 16)
                }
                .dsCardSurface(fill: .clear, radius: DS.Radius.control, border: DS.Line.divider)
                .padding(.top, 8)
            }
        }
        .padding(.vertical, 8)
    }
}
