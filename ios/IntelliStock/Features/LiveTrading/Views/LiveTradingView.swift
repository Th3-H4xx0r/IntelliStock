import SwiftUI

/// The live trading terminal for one instance — `LiveTradingScreen` in
/// `live_trading_screen.dart` — as an inset-grouped list (redesign spec
/// 2026-10-02): the equity hero with its chart, range and chart-style
/// pickers; the range and account figures as `StatGrid`s; the recent
/// executions and active positions as rows; the live logs. REAL money on
/// alpaca-main: Halt (the floating glass button), Close (a position's swipe
/// action and context menu) and Manual Order (the toolbar) keep every Dart
/// guard (typed confirmations, validation).
struct LiveTradingView: View {
    let instanceId: String

    @Environment(AppServices.self) private var services

    var body: some View {
        LiveTradingContent(instanceId: instanceId, services: services)
    }
}

private struct LiveTradingContent: View {
    let instanceId: String
    let services: AppServices

    @State private var model: LiveTradingModel
    @State private var chartStyle: LiveChartStyle = .area
    @State private var scrubIndex: Int?
    @State private var showHalt = false
    @State private var showOrder = false
    @State private var closeRequest: TypedConfirmRequest?
    @State private var closeRunning = false

    init(instanceId: String, services: AppServices) {
        self.instanceId = instanceId
        self.services = services
        _model = State(initialValue: LiveTradingModel(
            instanceId: instanceId,
            repository: { [unowned services] in services.liveRepository }
        ))
    }

    var body: some View {
        content
            .listStyle(.insetGrouped)
            .navigationTitle("Live Trading")
            .navigationSubtitle(instanceId)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                if let s = model.value, !s.notRunning {
                    ToolbarItem(placement: .primaryAction) {
                        Button("Manual Order", systemImage: "cart.badge.plus") { showOrder = true }
                    }
                }
            }
            .overlay(alignment: .bottom) { floatingLayer }
            .hidesChatButton()
            .task { await model.poll(lifecycle: services.lifecycle) }
            .sheet(isPresented: $showHalt) { LiveHaltSheet(model: model) }
            .sheet(isPresented: $showOrder) { LiveManualOrderSheet(model: model) }
            .typedConfirmAlert($closeRequest, isRunning: $closeRunning)
    }

    @ViewBuilder
    private var content: some View {
        switch model.state {
        case .loading:
            LiveTradingSkeleton()
        case .failed:
            List {
                Section {
                    ErrorRow(message: model.state.errorMessage ?? "") {
                        Task { await model.reload() }
                    }
                }
            }
        case .loaded(let s):
            loadedBody(s)
        }
    }

    private func loadedBody(_ s: LiveTradingState) -> some View {
        List {
            if s.notRunning {
                Section {
                    statusLine(s)
                }
                Section {
                    EmptyState(
                        systemImage: Symbol.named("power_off"),
                        title: "No live session",
                        subtitle: "This instance has no active broker session. Start the instance to begin live trading."
                    )
                    .listRowBackground(Color.clear)
                }
            } else if let ls = s.liveState {
                heroSection(s, ls)
                if let lookback = ls.lookback {
                    lookbackSection(lookback)
                }
                rangeSection(s, ls)
                accountSection(ls)
                executionsSection(ls.recentTrades)
                positionsSection(s)
            } else {
                Section {
                    statusLine(s)
                }
                LiveBodySkeleton()
            }
            Section("Live logs") {
                LiveLogsPanel(instanceId: instanceId)
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
            }
            // Room for the floating Halt button over the last rows.
            Color.clear
                .frame(height: 56)
                .listRowBackground(Color.clear)
                .accessibilityHidden(true)
        }
        // The hero starts right under the bar, as in Stocks.
        .contentMargins(.top, 0, for: .scrollContent)
        .refreshable { await model.refreshNow() }
    }

    // MARK: Status

    /// The session's state as a dot and a word, then any feed warnings
    /// (`_buildHeader`'s pill and chips).
    @ViewBuilder
    private func statusLine(_ s: LiveTradingState) -> some View {
        let ls = s.liveState
        let status = ls?.status.lowercased() ?? "unknown"
        let label = s.notRunning ? "Not running" : (ls?.status.dsSentenceCased ?? "Unknown")
        let color: Color = s.notRunning ? .secondary : (status == "active" ? DS.Palette.success : (status == "halted" ? DS.Palette.warning : .secondary))
        let brokerFailed = ls?.brokerFetchError != nil
        let stale = ls?.containerStale == true
        VStack(alignment: .leading, spacing: 4) {
            StatusDot(label, color: color, pulsing: status == "active", font: .footnote)
            if brokerFailed {
                warning("Broker offline", DS.Palette.danger)
            }
            if stale, !brokerFailed {
                warning("Container offline", .secondary)
            }
            if s.fetchError != nil, ls != nil {
                warning("Feed error", DS.Palette.danger)
            }
        }
    }

    private func warning(_ label: String, _ color: Color) -> some View {
        Label(label, systemImage: "exclamationmark.triangle.fill")
            .font(.footnote.weight(.semibold))
            .foregroundStyle(color)
    }

    // MARK: Hero

    private func heroSection(_ s: LiveTradingState, _ ls: LiveState) -> some View {
        let history = s.equityHistory
        let stats = RangeStats.from(history)
        var displayEquity = ls.equity
        if let idx = scrubIndex, let history, !history.values.isEmpty {
            displayEquity = history.values[min(max(idx, 0), history.values.count - 1)]
        }
        return Section {
            VStack(alignment: .leading, spacing: 0) {
                HeroValueHeader(
                    fmtMoney(displayEquity),
                    numericValue: displayEquity,
                    valueAnimation: scrubIndex == nil ? .snappy : nil,
                    change: "\(stats.isUp ? "+" : "")\(fmtMoney(stats.dollars)) (\(stats.isUp ? "+" : "")\(dartToStringAsFixed(stats.pct, 2))%)",
                    direction: stats.isUp ? .up : .down,
                    changeLabel: liveRangeLabel(s.currentRange)
                ) {
                    statusLine(s)
                }

                Group {
                    if let history, !history.isEmpty {
                        // The range the history belongs to, which lags the
                        // picker while a switch loads: the chart remounts and
                        // draws in when the new range's data lands.
                        let chartRange = s.equityHistoryRange ?? s.currentRange
                        LiveEquityChart(history: history, style: chartStyle, range: chartRange, height: 240) {
                            scrubIndex = $0
                        }
                        .id("\(chartRange)-\(chartStyle.rawValue)")
                    } else {
                        Text("No equity history yet — broker is fetching…")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity)
                            .frame(height: 240)
                    }
                }
                .padding(.top, 16)

                Picker("Range", selection: Binding(get: { s.currentRange }, set: { r in Task { await model.setRange(r) } })) {
                    ForEach(liveRanges, id: \.self) { Text($0).tag($0) }
                }
                .pickerStyle(.segmented)
                .padding(.top, 12)

                Picker("Chart style", selection: $chartStyle) {
                    ForEach(LiveChartStyle.allCases, id: \.self) { style in
                        Image(systemName: Symbol.named(style.symbolName))
                            .accessibilityLabel(style.accessibilityName)
                            .tag(style)
                    }
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 160)
                .frame(maxWidth: .infinity, alignment: .trailing)
                .padding(.top, 10)
            }
            .padding(.vertical, 4)
            .listRowBackground(Color.clear)
            .listRowSeparator(.hidden)
        }
    }

    // MARK: Lookback

    private func lookbackSection(_ lb: Lookback) -> some View {
        Section {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .firstTextBaseline) {
                    Text("\(lb.specName)  ·  \(lb.startDate) → \(lb.endDate)")
                        .font(.subheadline)
                        .lineLimit(1)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    HStack(alignment: .firstTextBaseline, spacing: 0) {
                        Text("\(lb.current)")
                            .font(.headline.monospacedDigit())
                        Text("/\(lb.total)")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
                ProgressView(value: lb.pct / 100)
                    .tint(DS.Palette.info)
                Text("\(dartToStringAsFixed(lb.pct, 0))%  ·  \(lb.currentDate)")
                    .font(.footnote.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            .padding(.vertical, 4)
            .accessibilityElement(children: .combine)
        } header: {
            Text("Historic lookback training")
        } footer: {
            Text("Trades deferred until warmup completes.")
        }
    }

    // MARK: Figures

    @ViewBuilder
    private func rangeSection(_ s: LiveTradingState, _ ls: LiveState) -> some View {
        let stats = RangeStats.from(s.equityHistory)
        if stats.high != nil {
            Section("Performance") {
                StatGrid(columns: 2) {
                    StatCell(label: "Range high", value: fmtMoney(stats.high))
                    StatCell(label: "Range low", value: fmtMoney(stats.low))
                    StatCell(label: "Day P&L", value: fmtPct(ls.dayPnlPct), valueColor: pnlColor(ls.dayPnl))
                    StatCell(label: "Total P&L", value: fmtPct(ls.totalPnlPct), valueColor: pnlColor(ls.totalPnl))
                }
                .padding(.vertical, 4)
            }
        }
    }

    private func accountSection(_ ls: LiveState) -> some View {
        Section("Account") {
            StatGrid(columns: 2) {
                StatCell(label: "Cash", value: fmtMoney(ls.cash))
                StatCell(label: "Buying power", value: fmtMoney(ls.buyingPower))
                StatCell(label: "Total P&L", value: fmtMoney(ls.totalPnl), valueColor: pnlColor(ls.totalPnl))
                StatCell(label: "Uptime", value: fmtDuration(ls.uptimeSec))
            }
            .padding(.vertical, 4)
        }
    }

    // MARK: Executions and positions

    private func executionsSection(_ trades: [Trade]) -> some View {
        Section {
            if trades.isEmpty {
                Text("No executions recorded yet.")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(Array(trades.enumerated()), id: \.offset) { _, t in
                    LiveTradeRow(trade: t)
                }
            }
        } header: {
            DSSectionHeader("Recent executions") {
                Text("\(trades.count)")
            }
        }
    }

    private func positionsSection(_ s: LiveTradingState) -> some View {
        let list = s.liveState?.positions ?? []
        return Section {
            if list.isEmpty {
                Text("No open positions.")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(Array(list.enumerated()), id: \.offset) { _, p in
                    LivePositionRow(
                        position: p,
                        chartStyle: chartStyle,
                        range: s.currentRange,
                        historicals: s.positionHistoricals[p.symbol] ?? [],
                        historicalsRange: s.positionHistoricalsRange
                    )
                    .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                        if p.canClose {
                            Button("Close", systemImage: Symbol.named("logout"), role: .destructive) {
                                confirmClose(p.symbol)
                            }
                            .disabled(closeRunning)
                        }
                    }
                    .contextMenu {
                        if p.canClose {
                            Button("Close", systemImage: Symbol.named("logout"), role: .destructive) {
                                confirmClose(p.symbol)
                            }
                            .disabled(closeRunning)
                        }
                    }
                    .accessibilityActions {
                        if p.canClose, !closeRunning {
                            Button("Close") { confirmClose(p.symbol) }
                        }
                    }
                }
            }
        } header: {
            DSSectionHeader("Active positions") {
                Text("\(list.count)")
            }
        } footer: {
            if list.contains(where: \.canClose) {
                Text("Swipe a position left, or touch and hold it, to close it.")
            }
        }
    }

    /// The close-position typed confirmation (`_confirmClosePosition`).
    private func confirmClose(_ symbol: String) {
        closeRequest = TypedConfirmRequest(
            title: "Close Position",
            body: "Submit a market sell for the full quantity of \(symbol)",
            phrase: "CLOSE \(symbol)",
            confirmLabel: "Submit Sell",
            role: .destructive,
            onConfirm: { await model.runCommand("close_position", ["symbol": .string(symbol)]) }
        )
    }

    // MARK: Floating layer

    /// The command toast and the sticky Halt button (Liquid Glass: the
    /// floating functional layer only), laid out against the bottom safe
    /// area, so they sit above the tab bar's chat accessory.
    @ViewBuilder
    private var floatingLayer: some View {
        let s = model.value
        VStack(alignment: .trailing, spacing: 12) {
            if let toast = s?.commandToast {
                LiveCommandToastView(toast: toast) { model.dismissToast() }
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }
            if let s, !s.notRunning {
                Button {
                    showHalt = true
                } label: {
                    Label("Halt", systemImage: Symbol.named("block"))
                        .font(.subheadline.weight(.bold))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 4)
                }
                .buttonStyle(.glass)
                .tint(DS.Palette.danger)
                .foregroundStyle(DS.Palette.danger)
                .accessibilityHint("Opens the halt confirmation")
            }
        }
        .padding(.horizontal, 16)
        .padding(.bottom, 16)
        .frame(maxWidth: .infinity, alignment: .trailing)
        .animation(.snappy, value: s?.commandToast)
    }
}

/// A command's progress (`_buildCommandToast`): pending spinner, completed
/// check or failed mark; the command and its status; the error or the
/// result map.
private struct LiveCommandToastView: View {
    let toast: CommandToast
    let onDismiss: () -> Void

    var body: some View {
        let color: Color = toast.isPending ? DS.Palette.info : (toast.status == "completed" ? DS.Palette.success : DS.Palette.danger)
        HStack(spacing: 8) {
            Group {
                if toast.isPending {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: Symbol.named(toast.status == "completed" ? "check_circle" : "error"))
                        .foregroundStyle(color)
                }
            }
            .frame(width: 20)
            VStack(alignment: .leading, spacing: 1) {
                Text("\(liveCommandWords(toast.type)) · \(liveCommandWords(toast.status))")
                    .font(.footnote.weight(.semibold))
                if let error = toast.error {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(DS.Palette.danger)
                } else if let result = toast.result, !result.isEmpty {
                    Text(JSON.object(result).dartDescription)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Button(action: onDismiss) {
                Image(systemName: Symbol.named("close"))
                    .font(.caption.weight(.semibold))
                    .frame(width: 44, height: 44)
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .accessibilityLabel("Dismiss")
        }
        .padding(.leading, 12)
        .glassEffect(.regular, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}

/// A command or status id in words: `close_position` → `Close position`.
nonisolated func liveCommandWords(_ raw: String) -> String {
    raw.replacingOccurrences(of: "_", with: " ").lowercased().dsSentenceCased
}

/// The first-load skeleton.
private struct LiveTradingSkeleton: View {
    var body: some View {
        List {
            Section {
                HeroValueHeader("$00,000.00", change: "+$000.00 (+0.00%)", status: "Active")
                    .listRowBackground(Color.clear)
            }
            LiveBodySkeleton()
        }
        .redacted(reason: .placeholder)
        .disabled(true)
        .accessibilityLabel("Loading")
    }
}

/// The body skeleton while the live state has not arrived.
private struct LiveBodySkeleton: View {
    var body: some View {
        Section {
            Skeleton(height: 240, radius: 12)
                .listRowBackground(Color.clear)
        }
        Section("Account") {
            StatGrid(columns: 2) {
                StatCell(label: "Cash", value: "$00,000.00")
                StatCell(label: "Buying power", value: "$00,000.00")
            }
            .redacted(reason: .placeholder)
        }
        .accessibilityHidden(true)
    }
}
