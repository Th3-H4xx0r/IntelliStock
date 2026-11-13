import SwiftUI

/// The live trading terminal for one instance — `LiveTradingScreen` in
/// `live_trading_screen.dart`, styled as the web's `LiveTradingView.vue`: the
/// session's status line; the historic lookback card while warming up; the
/// equity card (portfolio equity and uptime, the chart, the range and
/// chart-style controls, the range figures); cash, buying power and total
/// P&L; the recent executions and active positions as card rows; the live
/// logs. REAL money on alpaca-main: Halt (the floating button), Close (a
/// position's swipe action and context menu) and Manual order (the toolbar)
/// keep every Dart guard (typed confirmations, validation).
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
            .navigationTitle("Live trading")
            .navigationSubtitle(instanceId)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                if let s = model.value, !s.notRunning {
                    ToolbarItem(placement: .primaryAction) {
                        Button("Manual order", systemImage: "cart.badge.plus") { showOrder = true }
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
                DSSection(style: .plain) {
                    ErrorRow(message: model.state.errorMessage ?? "") {
                        Task { await model.reload() }
                    }
                    .dsClearRow(top: 8)
                }
            }
            .dsList()
        case .loaded(let s):
            loadedBody(s)
        }
    }

    private func loadedBody(_ s: LiveTradingState) -> some View {
        List {
            Section {
                statusLine(s)
                    .dsClearRow(top: 4)
            }
            .listSectionSpacing(16)
            if s.notRunning {
                DSSection {
                    EmptyState(
                        title: "No live session",
                        subtitle: "This instance has never booted a broker, or its broker is already shut down. Start the instance to begin a live session"
                    )
                }
            } else if let ls = s.liveState {
                if let lookback = ls.lookback {
                    lookbackSection(lookback)
                }
                heroSection(s, ls)
                accountSection(ls)
                executionsSection(ls.recentTrades)
                positionsSection(s)
            } else {
                LiveBodySkeleton()
            }
            DSSection("Live logs", style: .plain) {
                LiveLogsPanel(instanceId: instanceId)
                    .dsClearRow()
            }
            // Room for the floating Halt button over the last rows.
            Color.clear
                .frame(height: 56)
                .dsClearRow()
                .accessibilityHidden(true)
        }
        .dsList()
        // The status line starts right under the bar, as the web's header does.
        .contentMargins(.top, 8, for: .scrollContent)
        .refreshable { await model.refreshNow() }
    }

    // MARK: Status

    /// The session's state as a dot and a word, then any feed warnings, each
    /// a dot and a word too (`_buildHeader`'s pill and chips; the web's header).
    @ViewBuilder
    private func statusLine(_ s: LiveTradingState) -> some View {
        let ls = s.liveState
        let status = ls?.status.lowercased() ?? "unknown"
        let label = s.notRunning ? "Not running" : (ls?.status.lowercased().dsSentenceCased ?? "Unknown")
        let tone: DSStatusTone = s.notRunning ? .off : (status == "active" ? .live : (status == "halted" ? .warn : .off))
        let brokerFailed = ls?.brokerFetchError != nil
        let stale = ls?.containerStale == true
        FlowLayout(spacing: 16, runSpacing: 6) {
            StatusDot(label, tone: tone, pulsing: status == "active")
            if brokerFailed {
                StatusDot("Broker offline", tone: .error, font: DS.Font.caption)
            }
            if stale, !brokerFailed {
                StatusDot("Container offline", tone: .off, font: DS.Font.caption)
            }
            if s.fetchError != nil, ls != nil {
                StatusDot("Feed error", tone: .error, font: DS.Font.caption)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Hero

    /// The equity card: "Portfolio equity" with the figure and the range's
    /// change, uptime on the right, the chart, the range and chart-style
    /// controls, then the range high, low, day and total P&L under a divider.
    private func heroSection(_ s: LiveTradingState, _ ls: LiveState) -> some View {
        let history = s.equityHistory
        let stats = RangeStats.from(history)
        var displayEquity = ls.equity
        if let idx = scrubIndex, let history, !history.values.isEmpty {
            displayEquity = history.values[min(max(idx, 0), history.values.count - 1)]
        }
        return Section {
            Card {
                VStack(alignment: .leading, spacing: 0) {
                    HStack(alignment: .top, spacing: 12) {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Portfolio equity")
                                .font(DS.Font.body)
                                .foregroundStyle(DS.Text.secondary)
                            HeroValueHeader(
                                fmtMoney(displayEquity),
                                numericValue: displayEquity,
                                valueAnimation: scrubIndex == nil ? DSMotion.ease(0.3) : nil,
                                change: "\(stats.isUp ? "+" : "")\(fmtMoney(stats.dollars)) (\(stats.isUp ? "+" : "")\(dartToStringAsFixed(stats.pct, 2))%)",
                                direction: stats.isUp ? .up : .down,
                                changeLabel: liveRangeLabel(s.currentRange)
                            ) {
                                EmptyView()
                            }
                        }
                        VStack(alignment: .trailing, spacing: 4) {
                            Text("Uptime")
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Text.muted)
                            Text(fmtDuration(ls.uptimeSec))
                                .font(DS.Font.body)
                                .foregroundStyle(DS.Text.label)
                                .monospacedDigit()
                        }
                        .accessibilityElement(children: .combine)
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
                                .font(DS.Font.body)
                                .foregroundStyle(DS.Text.muted)
                                .frame(maxWidth: .infinity)
                                .frame(height: 240)
                        }
                    }
                    .padding(.top, 16)

                    // Seven ranges fit a phone's card at their natural widths
                    // ("YTD" truncates when they are forced equal); on a
                    // narrower phone the control scrolls sideways.
                    let rangeControl = DSSegmentedControl(
                        "Range",
                        selection: Binding(get: { s.currentRange }, set: { r in Task { await model.setRange(r) } }),
                        options: liveRanges
                    ) { $0 }
                    ViewThatFits(in: .horizontal) {
                        rangeControl
                        ScrollView(.horizontal, showsIndicators: false) { rangeControl }
                    }
                    .padding(.top, 12)

                    LiveChartStyleControl(selection: $chartStyle)
                        .frame(maxWidth: .infinity, alignment: .trailing)
                        .padding(.top, 8)

                    if stats.high != nil {
                        DSDivider()
                            .padding(.top, 20)
                        StatGrid(columns: 2, horizontalSpacing: 16, verticalSpacing: 16) {
                            StatCell(label: "Range high") { rangeFigure(fmtMoney(stats.high)) }
                            StatCell(label: "Range low") { rangeFigure(fmtMoney(stats.low)) }
                            StatCell(label: "Day P&L") { rangeFigure(fmtPct(ls.dayPnlPct), color: pnlColor(ls.dayPnl)) }
                            StatCell(label: "Total P&L") { rangeFigure(fmtPct(ls.totalPnlPct), color: pnlColor(ls.totalPnl)) }
                        }
                        .padding(.top, 20)
                    }
                }
            }
            .dsClearRow()
        }
    }

    /// A range figure: 14/500 (the web's `text-sm font-medium`).
    private func rangeFigure(_ text: String, color: Color = DS.Text.soft) -> some View {
        Text(text)
            .font(DS.Font.bodyMedium)
            .foregroundStyle(color)
    }

    // MARK: Lookback

    /// The warmup card (the web's lookback banner): what is replaying and
    /// over which dates, the step count, a thin bar, and the caveat.
    private func lookbackSection(_ lb: Lookback) -> some View {
        let fraction = min(max(lb.pct / 100, 0), 1)
        return DSSection {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Historic lookback training")
                            .font(DS.Font.bodyMedium)
                            .foregroundStyle(DS.Text.soft)
                        Text("\(lb.specName) · \(lb.startDate) → \(lb.endDate)")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                            .monospacedDigit()
                            .lineLimit(1)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    VStack(alignment: .trailing, spacing: 4) {
                        HStack(alignment: .firstTextBaseline, spacing: 0) {
                            Text("\(lb.current)")
                                .font(DS.Font.figure)
                                .foregroundStyle(DS.Text.primary)
                            Text("/\(lb.total)")
                                .font(DS.Font.body)
                                .foregroundStyle(DS.Text.muted)
                        }
                        Text("\(dartToStringAsFixed(lb.pct, 0))% · \(lb.currentDate)")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                    }
                    .monospacedDigit()
                }
                Capsule()
                    .fill(DS.Surface.raised)
                    .frame(height: 4)
                    .overlay(alignment: .leading) {
                        GeometryReader { geo in
                            Capsule()
                                .fill(DS.Text.label)
                                .frame(width: geo.size.width * fraction)
                        }
                    }
                    .accessibilityHidden(true)
                Text("Trades are deferred until warmup completes. The Nexus strategy needs this context before it can make day-1 decisions.")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.vertical, 8)
            .accessibilityElement(children: .combine)
        }
    }

    // MARK: Figures

    /// Cash, buying power and total P&L in one card, split by hairlines (the
    /// web's compact stats).
    private func accountSection(_ ls: LiveState) -> some View {
        Section {
            Card(padding: 0) {
                HStack(spacing: 0) {
                    accountCell("Cash", fmtMoney(ls.cash))
                    DSDivider(vertical: true)
                    accountCell("Buying power", fmtMoney(ls.buyingPower))
                    DSDivider(vertical: true)
                    accountCell("Total P&L", fmtMoney(ls.totalPnl), color: pnlColor(ls.totalPnl))
                }
                .fixedSize(horizontal: false, vertical: true)
            }
            .dsClearRow()
        }
    }

    private func accountCell(_ label: String, _ value: String, color: Color = DS.Text.primary) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .lineLimit(1)
            Text(value)
                .font(DS.Font.baseMedium)
                .foregroundStyle(color)
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    // MARK: Executions and positions

    private func executionsSection(_ trades: [Trade]) -> some View {
        DSSection("Recent executions", subtitle: "\(trades.count) recorded") {
            if trades.isEmpty {
                EmptyState(title: "No executions recorded yet")
            } else {
                ForEach(Array(trades.enumerated()), id: \.offset) { _, t in
                    LiveTradeRow(trade: t)
                }
            }
        }
    }

    private func positionsSection(_ s: LiveTradingState) -> some View {
        let list = s.liveState?.positions ?? []
        return DSSection("Active positions", subtitle: "\(list.count) open") {
            if list.isEmpty {
                EmptyState(title: "No open positions")
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
                            .tint(DS.Swipe.destructive)
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
        } footer: {
            if list.contains(where: \.canClose) {
                Text("Swipe a position left, or touch and hold it, to close it.")
            }
        }
    }

    /// The close-position typed confirmation (`_confirmClosePosition`).
    private func confirmClose(_ symbol: String) {
        closeRequest = TypedConfirmRequest(
            title: "Close position",
            body: "Submit a market sell for the full quantity of \(symbol)",
            phrase: "CLOSE \(symbol)",
            confirmLabel: "Submit sell",
            role: .destructive,
            onConfirm: { await model.runCommand("close_position", ["symbol": .string(symbol)]) }
        )
    }

    // MARK: Floating layer

    /// The command toast and the sticky Halt button (the web's mobile halt:
    /// a raised capsule with a loss hairline and loss text), laid out against
    /// the bottom safe area, so they sit above the tab bar's chat accessory.
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
                        .font(DS.Font.bodyMedium)
                        .foregroundStyle(DS.Palette.loss)
                        .padding(.horizontal, 20)
                        .frame(height: 48)
                        .background(DS.Surface.raised, in: Capsule())
                        .overlay {
                            Capsule().strokeBorder(DS.Palette.loss.opacity(0.3), lineWidth: DS.Line.width)
                        }
                        // A floating control over the list: the one place a shadow stays.
                        .shadow(color: .black.opacity(0.5), radius: 12, y: 8)
                        .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityHint("Opens the halt confirmation")
            }
        }
        .padding(.horizontal, 16)
        .padding(.bottom, 16)
        .frame(maxWidth: .infinity, alignment: .trailing)
        .animation(DSMotion.ease(0.2), value: s?.commandToast)
    }
}

/// A command's progress (`_buildCommandToast`), as the web's toast: a
/// spinner, a gain check or a loss mark; the command with its status; the
/// error or the result map; on a raised card with a loss hairline when it
/// failed.
private struct LiveCommandToastView: View {
    let toast: CommandToast
    let onDismiss: () -> Void

    var body: some View {
        let failed = !toast.isPending && toast.status != "completed"
        HStack(alignment: .top, spacing: 10) {
            Group {
                if toast.isPending {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: Symbol.named(toast.status == "completed" ? "check_circle" : "error"))
                        .font(.system(size: 16))
                        .foregroundStyle(failed ? DS.Palette.loss : DS.Palette.gain)
                }
            }
            .frame(width: 20, height: 20)
            .padding(.top, 12)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 4) {
                    Text(liveCommandWords(toast.type))
                        .foregroundStyle(DS.Text.primary)
                    Text("· \(liveCommandWords(toast.status).lowercased())")
                        .foregroundStyle(DS.Text.muted)
                }
                .font(DS.Font.body)
                if let error = toast.error {
                    Text(error)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Palette.loss)
                } else if let result = toast.result, !result.isEmpty {
                    Text(JSON.object(result).dartDescription)
                        .font(DS.Font.mono)
                        .foregroundStyle(DS.Text.secondary)
                        .lineLimit(1)
                }
            }
            .padding(.vertical, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
            Button(action: onDismiss) {
                Image(systemName: Symbol.named("close"))
            }
            .buttonStyle(.dsIcon(side: 44))
            .accessibilityLabel("Dismiss")
        }
        .padding(.leading, 14)
        .dsCardSurface(fill: DS.Surface.raised, border: failed ? DS.Palette.loss.opacity(0.3) : DS.Line.input)
        // Floating over the list: the one place a shadow stays.
        .shadow(color: .black.opacity(0.5), radius: 20, y: 16)
        .accessibilityElement(children: .combine)
    }
}

/// A command or status id in words: `close_position` → `Close position`.
nonisolated func liveCommandWords(_ raw: String) -> String {
    raw.replacingOccurrences(of: "_", with: " ").lowercased().dsSentenceCased
}

/// The first-load skeleton: static blocks shaped like the status line and
/// the equity card.
private struct LiveTradingSkeleton: View {
    var body: some View {
        List {
            Section {
                Skeleton.line(width: 72, height: 14)
                    .dsClearRow(top: 8)
            }
            Section {
                Card {
                    VStack(alignment: .leading, spacing: 10) {
                        Skeleton.line(width: 110, height: 14)
                        Skeleton(width: 220, height: 40)
                        Skeleton.line(width: 150, height: 14)
                        Skeleton(height: 240, radius: DS.Radius.control)
                            .padding(.top, 8)
                    }
                }
                .dsClearRow()
            }
            LiveBodySkeleton(chart: false)
        }
        .dsList()
        .disabled(true)
        .accessibilityLabel("Loading")
    }
}

/// The body skeleton while the live state has not arrived: the chart's
/// block, then the cash, buying power and P&L figures.
private struct LiveBodySkeleton: View {
    var chart = true

    var body: some View {
        if chart {
            Section {
                Card {
                    Skeleton(height: 240, radius: DS.Radius.control)
                }
                .dsClearRow()
            }
            .accessibilityHidden(true)
        }
        Section {
            Card {
                HStack(alignment: .top, spacing: 24) {
                    ForEach(0..<3, id: \.self) { _ in
                        VStack(alignment: .leading, spacing: 8) {
                            Skeleton.line(width: 56)
                            Skeleton.line(width: 80, height: 18)
                        }
                    }
                }
            }
            .dsClearRow()
        }
        .accessibilityHidden(true)
    }
}
