import SwiftUI

/// One backtest (`/backtests/:id`) — the web's `BacktestDetailView.vue`, as a
/// `.dsList()` under the large "Backtest #id" title: the status, period and
/// stocks with the run's progress, then the LLM-pause and Nexus-lookback
/// cards, the results card (total P&L over the run's figures), the TR
/// scorecard, crypto fees, LLM cost, strategy, logs, the portfolio chart,
/// P&L per stock, the per-stock charts and trades, and round-trip
/// statistics. Polls status every 3 s while the run is active.
///
/// Every lifecycle action (Pause, Resume, Stop, Rerun, Live playback,
/// Delete) sits in the toolbar's More menu, each still behind its
/// confirmation and the double-submit guard.
struct BacktestDetailView: View {
    let id: String

    @Environment(AppServices.self) private var services
    @State private var model: BacktestDetailModel?
    @State private var expandedStocks: Set<String> = []
    @State private var decisionLimits: [String: Int] = [:]
    @State private var strategyOpen = false
    @State private var confirm: ConfirmRequest?
    @State private var toast: Toast?
    /// A confirmed action in flight (no double submit, rerun never double-posts).
    @State private var acting = false

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    static let decisionPage = 5

    var body: some View {
        Group {
            if let model {
                content(model)
            } else {
                Color.clear
            }
        }
        .background(DS.Surface.page)
        .navigationTitle("Backtest #\(id)")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            // Only once the summary is on screen, where the web's buttons are.
            if let model, model.summary != nil, !model.loading, model.error == nil {
                ToolbarItem(placement: .topBarTrailing) { actionMenu(model) }
            }
        }
        .confirmAlert($confirm)
        .toast($toast)
        .task(id: id) {
            let m = model?.id == id ? model! : BacktestDetailModel(id: id, repository: { [services] in services.backtestRepository })
            model = m
            await m.run(lifecycle: services.lifecycle)
        }
    }

    @ViewBuilder
    private func content(_ model: BacktestDetailModel) -> some View {
        if model.loading {
            skeleton
        } else if let error = model.error {
            List {
                Section {
                    ErrorRow(message: error, onRetry: { Task { await model.load() } })
                        .dsClearRow(top: 8)
                }
            }
            .dsList()
        } else if let summary = model.summary {
            loaded(model, summary)
        } else {
            pending(model)
        }
    }

    /// The loading shape (the web's): the status line, then the results card
    /// and a chart-sized block.
    private var skeleton: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 32) {
                    Skeleton(width: 260, height: 14)
                    Skeleton(height: 132, radius: DS.Radius.card)
                    Skeleton(height: 240, radius: DS.Radius.card)
                }
                .dsClearRow(top: 4)
            }
        }
        .dsList()
        .disabled(true)
        .accessibilityLabel("Loading")
    }

    // MARK: Toolbar menu

    /// The web's action buttons, as a More menu: run controls, then Rerun
    /// and Live playback, then Delete.
    private func actionMenu(_ model: BacktestDetailModel) -> some View {
        let s = model.currentStatus.lowercased()
        let isRunning = s == "running"
        let isPaused = s == "paused" || s == "paused_llm_critical"
        let canStop = isRunning || isPaused || s == "queued"
        return ToolbarMenu {
            if isRunning || isPaused || canStop {
                Section {
                    if isRunning {
                        Button {
                            ask(model, "pause")
                        } label: {
                            Label("Pause", systemImage: Symbol.named("pause_circle"))
                        }
                    }
                    if isPaused {
                        Button {
                            ask(model, "resume")
                        } label: {
                            Label("Resume", systemImage: Symbol.named("play_circle"))
                        }
                    }
                    if canStop {
                        Button(role: .destructive) {
                            ask(model, "stop")
                        } label: {
                            Label("Stop", systemImage: Symbol.named("stop_circle"))
                        }
                    }
                }
                .disabled(acting)
            }
            Section {
                Button {
                    askRerun(model)
                } label: {
                    Label("Rerun", systemImage: Symbol.named("replay"))
                }
                .disabled(acting)
                Button {
                    services.router.push(.backtestPlayback(id))
                } label: {
                    Label("Live playback", systemImage: "film")
                }
            }
            Section {
                Button(role: .destructive) {
                    ask(model, "delete")
                } label: {
                    Label("Delete", systemImage: "trash")
                }
                .disabled(acting)
            }
        }
    }

    // MARK: Loaded

    private func loaded(_ model: BacktestDetailModel, _ summary: BacktestSummary) -> some View {
        List {
            header(model, summary)
            BacktestLlmPauseBanner(summary: summary)
            if let lookback = model.nexusLookback {
                BacktestNexusLookbackBanner(lookback: lookback)
            }
            BacktestResultsCard(summary: summary, elapsed: model.elapsedSeconds)
            if let card = summary.trScorecard {
                BacktestTrScorecardSection(card: card)
            }
            BacktestCryptoFeesCard(summary: summary)
            BacktestLlmCostCard(
                llmCost: model.llmCost,
                loading: model.llmCostLoading,
                error: model.llmCostError
            )
            if let schema = summary.strategySchema {
                BacktestStrategySection(schema: schema, strategyId: summary.strategyId, open: $strategyOpen)
            }
            BacktestLogsSection(id: id, model: model)
            if let graph = model.graphData, !graph.portfolioValueHistory.isEmpty {
                DSSection("Portfolio value over time", style: .plain) {
                    BacktestPortfolioChart(history: graph.portfolioValueHistory, startValue: summary.portfolioStartValue?.double)
                        .dsClearRow()
                }
            }
            if let perStock = summary.pnlPerStock, !perStock.isEmpty, !summary.tickers.isEmpty {
                BacktestPnlPerStockCard(summary: summary)
            }
            if let graph = model.graphData, !summary.tickers.isEmpty {
                stocks(summary, graph)
            }
            BacktestRoundTripStats(summary: summary)
        }
        .dsList()
        // The web's LLM-cost refresh button folds into pull-to-refresh.
        .refreshable {
            await model.load()
            await model.refreshLlmCost()
        }
    }

    /// The web header's subtitle — the status as a dot and a word, the
    /// period and the stocks — then the run's progress while it is active.
    private func header(_ model: BacktestDetailModel, _ summary: BacktestSummary) -> some View {
        let status = model.currentStatus
        return Section {
            VStack(alignment: .leading, spacing: 16) {
                FlowLayout(spacing: 6, runSpacing: 4) {
                    StatusDot(
                        BacktestStatusText.label(status),
                        status: status,
                        pulsing: status.lowercased() == "running",
                        font: DS.Font.pageSub
                    )
                    separator
                    Text("\(summary.startDate ?? "?") → \(summary.endDate ?? "?")")
                        .monospacedDigit()
                    if !summary.tickers.isEmpty {
                        separator
                        Text(summary.tickers.joined(separator: ", "))
                    }
                }
                .font(DS.Font.pageSub)
                .foregroundStyle(DS.Text.muted)
                if let progress = model.progress, ["running", "queued", "pending"].contains(status.lowercased()) {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Text("Progress").foregroundStyle(DS.Text.muted)
                            Spacer()
                            Text("\(Int(progress.double.rounded()))%")
                                .foregroundStyle(DS.Text.label)
                                .monospacedDigit()
                        }
                        .font(DS.Font.caption)
                        BacktestProgressBar(fraction: progress.double / 100)
                    }
                    .accessibilityElement(children: .combine)
                }
            }
            .dsClearRow(top: 0, bottom: 4)
        }
    }

    private var separator: some View {
        Text("·").foregroundStyle(DS.Text.faint).accessibilityHidden(true)
    }

    private func ask(_ model: BacktestDetailModel, _ action: String) {
        let meta = BacktestDetailModel.actionMeta(action)
        confirm = ConfirmRequest(
            title: meta.title,
            body: "\(meta.body)\n\nBacktest #\(id)",
            confirmLabel: meta.verb,
            role: action == "stop" || action == "delete" ? .destructive : nil,
            onConfirm: {
                acting = true
                defer { acting = false }
                if let err = await model.performAction(action) {
                    throw ApiError(message: err)
                }
                if action == "delete" {
                    if services.router.stack(for: services.router.tab).isEmpty {
                        services.router.go("/backtests")
                    } else {
                        services.router.pop()
                    }
                }
            },
            onError: { error in
                if !error.isCancellation { toast = Toast(KalshiFormat.errorText(error), style: .error) }
            }
        )
    }

    private func askRerun(_ model: BacktestDetailModel) {
        confirm = ConfirmRequest(
            title: "Rerun backtest",
            body: "A new backtest will be created with the same settings.\n\nBacktest #\(id)",
            confirmLabel: "Rerun",
            role: nil,
            onConfirm: {
                guard !acting else { return }
                acting = true
                defer { acting = false }
                if let newId = try await model.rerun() {
                    services.router.go("/backtests/\(newId)")
                }
            },
            onError: { error in
                if !error.isCancellation { toast = Toast(KalshiFormat.errorText(error), style: .error) }
            }
        )
    }

    // MARK: Per-stock accordions

    private func stocks(_ summary: BacktestSummary, _ graph: BacktestGraphData) -> some View {
        DSSection("Stock charts and trades", subtitle: "\(summary.tickers.count) stocks") {
            ForEach(summary.tickers, id: \.self) { sym in
                BacktestStockAccordion(
                    sym: sym,
                    summary: summary,
                    graphData: graph,
                    expanded: expandedStocks.contains(sym),
                    decisionLimit: decisionLimits[sym] ?? Self.decisionPage,
                    onToggle: {
                        withAnimation(DSMotion.standard(reduceMotion: reduceMotion)) {
                            if expandedStocks.contains(sym) { expandedStocks.remove(sym) } else { expandedStocks.insert(sym) }
                        }
                    },
                    onShowMoreDecisions: {
                        decisionLimits[sym] = (decisionLimits[sym] ?? Self.decisionPage) + Self.decisionPage
                    }
                )
            }
        } accessory: {
            HStack(spacing: 8) {
                if !expandedStocks.isEmpty {
                    Button("Collapse all") {
                        expandedStocks.removeAll()
                        decisionLimits.removeAll()
                    }
                    .buttonStyle(.dsSecondary)
                }
                Button("Expand all") { expandedStocks.formUnion(summary.tickers) }
                    .buttonStyle(.dsSecondary)
            }
        }
    }

    // MARK: Pending (no summary)

    /// Not finished yet (`ui-empty`): the sentence, then the status and its
    /// progress, or the status alone.
    private func pending(_ model: BacktestDetailModel) -> some View {
        List {
            Section {
                Card(padding: 0) {
                    VStack(spacing: 12) {
                        Text("This backtest has not completed yet")
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.label)
                        if let progress = model.progress {
                            VStack(spacing: 8) {
                                HStack {
                                    Text(BacktestStatusText.label(model.currentStatus))
                                    Spacer()
                                    Text("\(Int(progress.double.rounded()))%").monospacedDigit()
                                }
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Text.muted)
                                BacktestProgressBar(fraction: progress.double / 100)
                            }
                            .frame(width: 224)
                        } else {
                            Text("Status: \(BacktestStatusText.label(model.currentStatus))")
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Text.muted)
                        }
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 40)
                    .padding(.horizontal, 24)
                }
                .dsClearRow(top: 8)
            }
        }
        .dsList()
    }
}
