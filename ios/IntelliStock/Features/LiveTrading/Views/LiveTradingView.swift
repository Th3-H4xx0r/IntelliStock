import SwiftUI

/// The live trading terminal for one instance — `LiveTradingScreen` in
/// `live_trading_screen.dart`. REAL money on alpaca-main: Halt, Close and
/// Manual Order keep every Dart guard (typed confirmations, validation).
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
            .background(DS.Surface.canvas)
            .navigationTitle("Live Trading")
            .navigationSubtitle(instanceId)
            .navigationBarTitleDisplayMode(.inline)
            .overlay(alignment: .bottom) { floatingLayer }
            // The floating chat button would cover the Halt button.
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
            ScrollView {
                ErrorRow(message: model.state.errorMessage ?? "") {
                    Task { await model.reload() }
                }
                .padding(24)
            }
        case .loaded(let s):
            loadedBody(s)
        }
    }

    private func loadedBody(_ s: LiveTradingState) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                header(s)
                if let lookback = s.liveState?.lookback, !s.notRunning {
                    lookbackBanner(lookback)
                }
                if s.notRunning {
                    EmptyState(
                        systemImage: Symbol.named("power_off"),
                        title: "No live session",
                        subtitle: "This instance has no active broker session. Start the instance to begin live trading."
                    )
                } else if let ls = s.liveState {
                    heroCard(s, ls)
                    secondaryStats(ls)
                    executions(ls.recentTrades)
                    positions(s)
                } else {
                    LiveBodySkeleton()
                }
                LiveLogsPanel(instanceId: instanceId)
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .padding(.bottom, 120)
        }
        .refreshable { await model.refreshNow() }
    }

    // MARK: Header

    private func header(_ s: LiveTradingState) -> some View {
        let ls = s.liveState
        let status = ls?.status.lowercased() ?? "unknown"
        let label = s.notRunning ? "NOT RUNNING" : (ls?.status.uppercased() ?? "UNKNOWN")
        let color: Color = s.notRunning ? .secondary : (status == "active" ? DS.Palette.success : (status == "halted" ? DS.Palette.warning : .secondary))
        let brokerFailed = ls?.brokerFetchError != nil
        let stale = ls?.containerStale == true
        return VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 12) {
                IconTile(systemImage: Symbol.named("monitoring"), color: DS.Palette.info, size: 44)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Live Trading Terminal")
                        .font(.title3.bold())
                    Text("Real-time positions & executions")
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                }
            }
            DashboardFlowLayout(spacing: 8) {
                StatusBadge(label: label, color: color, pulsing: status == "active")
                if brokerFailed {
                    warningChip("Broker offline", DS.Palette.danger)
                }
                if stale, !brokerFailed {
                    warningChip("Container offline", .secondary)
                }
                if s.fetchError != nil, ls != nil {
                    warningChip("Feed error", DS.Palette.danger)
                }
                if !s.notRunning {
                    Button(role: .destructive) {
                        showHalt = true
                    } label: {
                        Label("Halt", systemImage: Symbol.named("block"))
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    Button {
                        showOrder = true
                    } label: {
                        Label("Manual Order", systemImage: Symbol.named("add_shopping_cart"))
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .tint(DS.Palette.info)
                }
            }
        }
    }

    private func warningChip(_ label: String, _ color: Color) -> some View {
        Label(label, systemImage: Symbol.named("error"))
            .font(.caption2.weight(.semibold))
            .foregroundStyle(color)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(color.opacity(DS.tintFill), in: .rect(cornerRadius: 6, style: .continuous))
    }

    // MARK: Lookback

    private func lookbackBanner(_ lb: Lookback) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 6) {
                Image(systemName: Symbol.named("history"))
                    .foregroundStyle(DS.Palette.info)
                VStack(alignment: .leading, spacing: 1) {
                    Text("HISTORIC LOOKBACK TRAINING")
                        .font(.caption2.weight(.bold))
                        .tracking(0.8)
                        .foregroundStyle(DS.Palette.info)
                    Text("\(lb.specName)  ·  \(lb.startDate) → \(lb.endDate)")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                VStack(alignment: .trailing, spacing: 1) {
                    HStack(alignment: .firstTextBaseline, spacing: 0) {
                        Text("\(lb.current)")
                            .font(.title3.weight(.semibold).monospacedDigit())
                            .foregroundStyle(DS.Palette.info)
                        Text("/\(lb.total)")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    Text("\(dartToStringAsFixed(lb.pct, 0))%  ·  \(lb.currentDate)")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            ProgressView(value: lb.pct / 100)
                .tint(DS.Palette.info)
            Text("Trades deferred until warmup completes.")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(14)
        .background(DS.Palette.info.opacity(DS.tintFill), in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
        .accessibilityElement(children: .combine)
    }

    // MARK: Hero

    private func heroCard(_ s: LiveTradingState, _ ls: LiveState) -> some View {
        let history = s.equityHistory
        let stats = RangeStats.from(history)
        let changeColor = stats.isUp ? DS.Palette.up : DS.Palette.down
        var displayEquity = ls.equity
        if let idx = scrubIndex, let history, !history.values.isEmpty {
            displayEquity = history.values[min(max(idx, 0), history.values.count - 1)]
        }
        return Card(padding: 16) {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("PORTFOLIO EQUITY")
                            .font(.caption2.weight(.bold))
                            .tracking(1.0)
                            .foregroundStyle(.secondary)
                        Text(fmtMoney(displayEquity))
                            .font(.title.weight(.bold).monospacedDigit())
                            .contentTransition(.numericText(value: displayEquity))
                            .lineLimit(1)
                            .minimumScaleFactor(0.6)
                        HStack(spacing: 4) {
                            Image(systemName: Symbol.named(stats.isUp ? "arrow_upward" : "arrow_downward"))
                                .font(.caption.weight(.bold))
                                .accessibilityHidden(true)
                            Text("\(stats.isUp ? "+" : "")\(fmtMoney(stats.dollars))")
                                .font(.caption.weight(.bold).monospacedDigit())
                            Text("(\(stats.isUp ? "+" : "")\(dartToStringAsFixed(stats.pct, 2))%)")
                                .font(.caption.monospacedDigit())
                                .opacity(0.8)
                            Text(liveRangeLabel(s.currentRange))
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        .foregroundStyle(changeColor)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    VStack(alignment: .trailing, spacing: 2) {
                        Text("UPTIME")
                            .font(.caption2)
                            .tracking(0.8)
                            .foregroundStyle(.secondary)
                        Text(fmtDuration(ls.uptimeSec))
                            .font(.subheadline.monospacedDigit())
                    }
                }

                if let history, !history.isEmpty {
                    LiveEquityChart(history: history, style: chartStyle, range: s.currentRange, height: 240) {
                        scrubIndex = $0
                    }
                    .id("\(s.currentRange)-\(chartStyle.rawValue)")
                } else {
                    Text("No equity history yet — broker is fetching…")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity)
                        .frame(height: 240)
                }

                Picker("Range", selection: Binding(get: { s.currentRange }, set: { r in Task { await model.setRange(r) } })) {
                    ForEach(liveRanges, id: \.self) { Text($0).tag($0) }
                }
                .pickerStyle(.segmented)

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

                if stats.high != nil {
                    Divider()
                    HStack(alignment: .top) {
                        miniStat("RANGE HIGH", fmtMoney(stats.high))
                        miniStat("RANGE LOW", fmtMoney(stats.low))
                        miniStat("DAY P&L", fmtPct(ls.dayPnlPct), color: pnlColor(ls.dayPnl))
                        miniStat("TOTAL P&L", fmtPct(ls.totalPnlPct), color: pnlColor(ls.totalPnl))
                    }
                }
            }
        }
    }

    private func miniStat(_ label: String, _ value: String, color: Color = .primary) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption2)
                .tracking(0.5)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.caption.weight(.bold).monospacedDigit())
                .foregroundStyle(color)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    // MARK: Secondary stats, executions, positions

    private func secondaryStats(_ ls: LiveState) -> some View {
        HStack(spacing: 8) {
            statCard("CASH", fmtMoney(ls.cash))
            statCard("BUYING POWER", fmtMoney(ls.buyingPower))
            statCard("TOTAL P&L", fmtMoney(ls.totalPnl), color: pnlColor(ls.totalPnl))
        }
    }

    private func statCard(_ label: String, _ value: String, color: Color = .primary) -> some View {
        Card(padding: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(label)
                    .font(.caption2)
                    .tracking(0.8)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .dsMinimumScaleFactor(0.8, textStyle: .caption2)
                Text(value)
                    .font(.subheadline.weight(.semibold).monospacedDigit())
                    .foregroundStyle(color)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private func sectionHeader(_ title: String, _ count: Int) -> some View {
        HStack {
            Text(title)
                .font(.caption2.weight(.bold))
                .tracking(1.0)
                .foregroundStyle(.secondary)
                .accessibilityAddTraits(.isHeader)
            Spacer()
            Text("\(count)")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
    }

    private func executions(_ trades: [Trade]) -> some View {
        Card(padding: 14) {
            VStack(alignment: .leading, spacing: 8) {
                sectionHeader("RECENT EXECUTIONS", trades.count)
                if trades.isEmpty {
                    Text("No executions recorded yet.")
                        .font(.caption)
                        .italic()
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 16)
                } else {
                    ForEach(Array(trades.enumerated()), id: \.offset) { _, t in
                        LiveTradeRow(trade: t)
                    }
                }
            }
        }
    }

    private func positions(_ s: LiveTradingState) -> some View {
        let list = s.liveState?.positions ?? []
        return Card(padding: 14) {
            VStack(alignment: .leading, spacing: 8) {
                sectionHeader("ACTIVE POSITIONS", list.count)
                if list.isEmpty {
                    Text("No open positions.")
                        .font(.caption)
                        .italic()
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 16)
                } else {
                    ForEach(Array(list.enumerated()), id: \.offset) { _, p in
                        LivePositionCard(
                            position: p,
                            chartStyle: chartStyle,
                            range: s.currentRange,
                            historicals: s.positionHistoricals[p.symbol] ?? [],
                            closeDisabled: closeRunning,
                            onClose: { confirmClose(p.symbol) }
                        )
                    }
                }
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
    /// floating functional layer only).
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
/// check or failed mark; `TYPE · STATUS`; the error or the result map.
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
                Text("\(toast.type.uppercased()) · \(toast.status.uppercased())")
                    .font(.caption2.weight(.bold))
                    .tracking(0.5)
                if let error = toast.error {
                    Text(error)
                        .font(.caption2)
                        .foregroundStyle(DS.Palette.danger)
                } else if let result = toast.result, !result.isEmpty {
                    Text(JSON.object(result).dartDescription)
                        .font(.caption2)
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

/// The first-load skeleton.
private struct LiveTradingSkeleton: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 12) {
                    Skeleton.circle(44)
                    VStack(alignment: .leading, spacing: 6) {
                        Skeleton(height: 22, radius: 7)
                        Skeleton(width: 200, height: 12, radius: 5)
                    }
                }
                Skeleton(width: 260, height: 30, radius: 8)
                LiveBodySkeleton()
            }
            .padding(16)
        }
        .scrollDisabled(true)
        .accessibilityLabel("Loading")
    }
}

/// The body skeleton while the live state has not arrived.
private struct LiveBodySkeleton: View {
    var body: some View {
        VStack(spacing: 12) {
            Skeleton(height: 360, radius: DS.Radius.card)
            Skeleton(height: 150, radius: DS.Radius.control)
            Skeleton(height: 150, radius: DS.Radius.control)
        }
        .accessibilityHidden(true)
    }
}
