import SwiftUI

/// One backtest (`/backtests/:id`) — `BacktestDetailScreen`: status and
/// lifecycle actions, stats, AI credits, crypto fees, strategy, logs, the
/// portfolio chart, per-stock charts / trades / decision traces and round
/// trips. Polls status every 3 s while the run is active.
///
/// An inset-grouped list under the inline "Backtest #id" title. The P&L is the
/// hero, over the portfolio chart; every lifecycle action (Pause, Resume,
/// Stop, Rerun, Playback, Delete) sits in the toolbar's More menu, each still
/// behind its confirmation and the double-submit guard.
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

    static let decisionPage = 5

    var body: some View {
        Group {
            if let model {
                content(model)
            } else {
                Color.clear
            }
        }
        .background(DS.Surface.canvas)
        .navigationTitle("Backtest #\(id)")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            // Only once the summary is on screen, where the Dart chips were.
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
            ProgressView()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let error = model.error {
            List {
                Section {
                    ErrorRow(message: error, onRetry: { Task { await model.load() } })
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                }
            }
            .listStyle(.insetGrouped)
        } else if let summary = model.summary {
            loaded(model, summary)
        } else {
            pending(model)
        }
    }

    // MARK: Toolbar menu

    /// The Dart action chips, as a More menu: run controls, then Rerun and
    /// Playback, then Delete.
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
                    Label("Playback", systemImage: "film")
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
            hero(model, summary)
            BacktestLlmPauseBanner(summary: summary)
            if let lookback = model.nexusLookback {
                BacktestNexusLookbackBanner(lookback: lookback)
            }
            BacktestStatGrid(summary: summary, elapsed: model.elapsedSeconds)
            BacktestLlmCostCard(
                llmCost: model.llmCost,
                loading: model.llmCostLoading,
                error: model.llmCostError
            )
            BacktestCryptoFeesCard(summary: summary)
            if !summary.tickers.isEmpty {
                Section("Symbols") {
                    MarketsFlowLayout(spacing: 6, runSpacing: 6) {
                        ForEach(summary.tickers, id: \.self) { MarketsChip(text: $0) }
                    }
                    .padding(.vertical, 4)
                }
            }
            if let schema = summary.strategySchema {
                BacktestStrategySection(schema: schema, strategyId: summary.strategyId, open: $strategyOpen)
            }
            BacktestLogsSection(id: id, model: model)
            if let perStock = summary.pnlPerStock, !perStock.isEmpty {
                BacktestPnlPerStockCard(summary: summary)
            }
            if let graph = model.graphData, !summary.tickers.isEmpty {
                stocks(summary, graph)
            }
            BacktestRoundTripStats(summary: summary)
        }
        .listStyle(.insetGrouped)
        // The Dart AI-credits refresh button folds into pull-to-refresh.
        .refreshable {
            await model.load()
            await model.refreshLlmCost()
        }
    }

    /// The P&L as the hero, the status and dates as its status line, the
    /// run's progress while active, then the portfolio chart.
    private func hero(_ model: BacktestDetailModel, _ summary: BacktestSummary) -> some View {
        Section {
            VStack(alignment: .leading, spacing: DS.cardGroupSpacing) {
                HeroValueHeader(
                    fmtPnl(summary.pnl?.double),
                    numericValue: summary.pnl?.double,
                    change: fmtPct(summary.pnlPercent?.double),
                    direction: ChangeDirection(summary.pnl?.double)
                ) {
                    HStack(spacing: 8) {
                        StatusDot(model.currentStatus.dsSentenceCased, status: model.currentStatus, pulsing: model.isActive, font: .footnote)
                        Text(BacktestRowFormat.dateRange(summary.startDate, summary.endDate))
                    }
                }
                if let progress = model.progress, model.isActive {
                    VStack(spacing: 4) {
                        HStack {
                            Text("Progress").font(.footnote).foregroundStyle(.secondary)
                            Spacer()
                            Text("\(Int(progress.double.rounded()))%").font(.footnote.monospacedDigit()).foregroundStyle(.secondary)
                        }
                        ProgressView(value: min(max(progress.double / 100, 0), 1))
                    }
                }
                if let graph = model.graphData, !graph.portfolioValueHistory.isEmpty {
                    BacktestPortfolioChart(history: graph.portfolioValueHistory, startValue: summary.portfolioStartValue?.double)
                }
            }
            .padding(.vertical, 6)
        }
    }

    private func ask(_ model: BacktestDetailModel, _ action: String) {
        let meta = BacktestDetailModel.actionMeta(action)
        confirm = ConfirmRequest(
            title: meta.title,
            body: "\(meta.body)\n\nBacktest #\(id)",
            confirmLabel: String(meta.title.split(separator: " ").last ?? ""),
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
            title: "Rerun Backtest",
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
        Section {
            ForEach(summary.tickers, id: \.self) { sym in
                BacktestStockAccordion(
                    sym: sym,
                    summary: summary,
                    graphData: graph,
                    expanded: expandedStocks.contains(sym),
                    decisionLimit: decisionLimits[sym] ?? Self.decisionPage,
                    onToggle: {
                        withAnimation(.snappy) {
                            if expandedStocks.contains(sym) { expandedStocks.remove(sym) } else { expandedStocks.insert(sym) }
                        }
                    },
                    onShowMoreDecisions: {
                        decisionLimits[sym] = (decisionLimits[sym] ?? Self.decisionPage) + Self.decisionPage
                    }
                )
            }
        } header: {
            DSSectionHeader("Stock charts & trades") {
                HStack(spacing: 14) {
                    Button("Expand All") { expandedStocks.formUnion(summary.tickers) }
                    if !expandedStocks.isEmpty {
                        Button("Collapse All") {
                            expandedStocks.removeAll()
                            decisionLimits.removeAll()
                        }
                    }
                }
                .font(.footnote)
                .buttonStyle(.borderless)
            }
        }
    }

    // MARK: Pending (no summary)

    private func pending(_ model: BacktestDetailModel) -> some View {
        ContentUnavailableView {
            Label("This backtest has not completed yet.", systemImage: Symbol.named("hourglass_empty"))
        } description: {
            if let progress = model.progress {
                VStack(spacing: 4) {
                    HStack {
                        Text(model.currentStatus)
                        Spacer()
                        Text("\(Int(progress.double.rounded()))%")
                    }
                    .font(.footnote.monospacedDigit())
                    ProgressView(value: min(max(progress.double / 100, 0), 1))
                }
                .frame(width: 200)
                .padding(.top, 4)
            }
        }
    }
}
