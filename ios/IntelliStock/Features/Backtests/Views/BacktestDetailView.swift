import SwiftUI

/// One backtest (`/backtests/:id`) — `BacktestDetailScreen`: status and
/// lifecycle actions, stats, AI credits, crypto fees, strategy, logs, the
/// portfolio chart, per-stock charts / trades / decision traces and round
/// trips. Polls status every 3 s while the run is active.
struct BacktestDetailView: View {
    let id: String

    @Environment(AppServices.self) private var services
    @State private var model: BacktestDetailModel?
    @State private var expandedStocks: Set<String> = []
    @State private var decisionLimits: [String: Int] = [:]
    @State private var logsOpen = false
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
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if model.loading {
                    BacktestDetailSkeleton()
                } else if let error = model.error {
                    ErrorRow(message: error, onRetry: { Task { await model.load() } })
                } else if let summary = model.summary {
                    loaded(model, summary)
                } else {
                    pending(model)
                }
            }
            .padding(16)
        }
    }

    // MARK: Loaded

    @ViewBuilder
    private func loaded(_ model: BacktestDetailModel, _ summary: BacktestSummary) -> some View {
        header(model, summary)
        BacktestLlmPauseBanner(summary: summary)
        if let lookback = model.nexusLookback {
            BacktestNexusLookbackBanner(lookback: lookback)
        }
        BacktestStatGrid(summary: summary, elapsed: model.elapsedSeconds)
        BacktestLlmCostCard(
            llmCost: model.llmCost,
            loading: model.llmCostLoading,
            error: model.llmCostError,
            onRefresh: { Task { await model.refreshLlmCost() } }
        )
        BacktestCryptoFeesCard(summary: summary)
        if let schema = summary.strategySchema {
            BacktestStrategySection(schema: schema, strategyId: summary.strategyId, open: $strategyOpen)
        }
        BacktestLogsPanel(
            id: id,
            open: logsOpen,
            lines: model.logLines,
            loading: model.logsLoading,
            error: model.logsError,
            source: model.logSource,
            onToggle: {
                logsOpen.toggle()
                if !logsOpen || !model.logLines.isEmpty { return }
                Task { await model.loadLogs() }
            }
        )
        if let graph = model.graphData, !graph.portfolioValueHistory.isEmpty {
            BacktestPortfolioChart(history: graph.portfolioValueHistory, startValue: summary.portfolioStartValue?.double)
        }
        if let perStock = summary.pnlPerStock, !perStock.isEmpty {
            BacktestPnlPerStockCard(summary: summary)
        }
        if let graph = model.graphData, !summary.tickers.isEmpty {
            stocks(summary, graph)
        }
        BacktestRoundTripStats(summary: summary)
    }

    private func header(_ model: BacktestDetailModel, _ summary: BacktestSummary) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                IconTile(systemImage: Symbol.named("analytics"), color: DS.Palette.info, size: 44)
                VStack(alignment: .leading, spacing: 2) {
                    MarketsFlowLayout(spacing: 8) {
                        Text("Backtest #\(id)").font(.title3.bold())
                        StatusBadge(
                            label: model.currentStatus.uppercased(),
                            color: StatusBadge.color(forStatus: model.currentStatus),
                            pulsing: model.isActive
                        )
                    }
                    Text("\(summary.startDate ?? "?") → \(summary.endDate ?? "?")")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    if !summary.tickers.isEmpty {
                        Text(summary.tickers.joined(separator: ", "))
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                    }
                }
            }
            if let progress = model.progress, model.isActive {
                VStack(spacing: 4) {
                    HStack {
                        Text("Progress").font(.footnote).foregroundStyle(.secondary)
                        Spacer()
                        Text("\(Int(progress.double.rounded()))%").font(.caption.monospaced()).foregroundStyle(.secondary)
                    }
                    ProgressView(value: min(max(progress.double / 100, 0), 1)).tint(DS.Palette.info)
                }
            }
            actionCluster(model)
        }
    }

    private func actionCluster(_ model: BacktestDetailModel) -> some View {
        let s = model.currentStatus.lowercased()
        let isRunning = s == "running"
        let isPaused = s == "paused" || s == "paused_llm_critical"
        let canStop = isRunning || isPaused || s == "queued"
        return MarketsFlowLayout(spacing: 8, runSpacing: 8) {
            if isRunning { chip("Pause", "pause_circle", DS.Palette.accent) { ask(model, "pause") } }
            if isPaused { chip("Resume", "play_circle", DS.Palette.info) { ask(model, "resume") } }
            if canStop { chip("Stop", "stop_circle", DS.Palette.danger) { ask(model, "stop") } }
            chip("Rerun", "replay", DS.Palette.success) { askRerun(model) }
            chip("Playback", "movie", DS.Palette.warning, enabled: true) {
                services.router.push(.backtestPlayback(id))
            }
            chip("Delete", "delete_forever", DS.Palette.danger) { ask(model, "delete") }
        }
    }

    private func chip(_ label: String, _ icon: String, _ color: Color, enabled: Bool? = nil, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(label, systemImage: icon == "movie" ? "film" : (icon == "delete_forever" ? "trash" : Symbol.named(icon)))
                .font(.caption.weight(.semibold))
        }
        .buttonStyle(.bordered)
        .controlSize(.small)
        .tint(color)
        .disabled(!(enabled ?? !acting))
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
                if !marketsIsCancellation(error) { toast = Toast(KalshiFormat.errorText(error), style: .error) }
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
                if !marketsIsCancellation(error) { toast = Toast(KalshiFormat.errorText(error), style: .error) }
            }
        )
    }

    // MARK: Per-stock accordions

    private func stocks(_ summary: BacktestSummary, _ graph: BacktestGraphData) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("STOCK CHARTS & TRADES")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Expand All") { expandedStocks.formUnion(summary.tickers) }
                    .font(.footnote)
                if !expandedStocks.isEmpty {
                    Button("Collapse All") {
                        expandedStocks.removeAll()
                        decisionLimits.removeAll()
                    }
                    .font(.footnote)
                }
            }
            .buttonStyle(.borderless)
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
        }
    }

    // MARK: Pending (no summary)

    private func pending(_ model: BacktestDetailModel) -> some View {
        VStack(spacing: 12) {
            Image(systemName: Symbol.named("hourglass_empty"))
                .font(.system(size: 44))
                .foregroundStyle(.tertiary)
            Text("This backtest has not completed yet.")
                .foregroundStyle(.secondary)
            if let progress = model.progress {
                VStack(spacing: 4) {
                    HStack {
                        Text(model.currentStatus)
                        Spacer()
                        Text("\(Int(progress.double.rounded()))%")
                    }
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    ProgressView(value: min(max(progress.double / 100, 0), 1)).tint(DS.Palette.info)
                }
                .frame(width: 200)
                .padding(.top, 4)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 80)
    }
}
