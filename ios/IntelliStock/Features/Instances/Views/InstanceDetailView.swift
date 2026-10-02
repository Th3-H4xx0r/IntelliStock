import SwiftUI

/// One instance — `InstanceDetailScreen` in `instance_detail_screen.dart`:
/// header and run control, info / brokerage / strategy cards, the swing and
/// wheel lanes, stocks, live logs and the instance's backtests.
struct InstanceDetailView: View {
    let instanceId: String

    @Environment(AppServices.self) private var services

    var body: some View {
        InstanceDetailContent(instanceId: instanceId, services: services)
    }
}

/// The detail screen's sheets.
private enum InstanceDetailSheet: Identifiable {
    case clearState, addStock, linkBrokerage(String?), linkStrategy, createBacktest

    var id: String {
        switch self {
        case .clearState: "clear"
        case .addStock: "stock"
        case .linkBrokerage: "brokerage"
        case .linkStrategy: "strategy"
        case .createBacktest: "backtest"
        }
    }
}

private struct InstanceDetailContent: View {
    let instanceId: String
    let services: AppServices

    @State private var model: InstanceDetailModel
    @State private var signals: PendingSignalsModel
    @State private var wheel: WheelModel
    @State private var sheet: InstanceDetailSheet?
    @State private var confirm: ConfirmRequest?
    /// A confirmed unlink is running: the unlink buttons stay disabled.
    @State private var confirmRunning = false
    @State private var toast: Toast?
    @State private var toggling = false

    init(instanceId: String, services: AppServices) {
        self.instanceId = instanceId
        self.services = services
        _model = State(initialValue: InstanceDetailModel(
            instanceId: instanceId,
            repository: { [unowned services] in services.instanceRepository }
        ))
        _signals = State(initialValue: PendingSignalsModel(
            instanceId: instanceId,
            source: { [unowned services] in services.swingRepository }
        ))
        _wheel = State(initialValue: WheelModel(
            instanceId: instanceId,
            source: { [unowned services] in services.swingRepository }
        ))
    }

    var body: some View {
        content
            .background(DS.Surface.canvas)
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await model.refreshInstance() }
                    } label: {
                        Image(systemName: Symbol.named("refresh"))
                    }
                    .accessibilityLabel("Refresh")
                }
            }
            .task { if model.value == nil { await model.load() } }
            .task { await model.runUptimeTicker() }
            .task { await model.runProgressPoll() }
            .sheet(item: $sheet) { sheetView($0) }
            .confirmAlert($confirm, isRunning: $confirmRunning)
            .toast($toast)
    }

    private var title: String {
        guard let inst = model.value?.instance else { return "" }
        return inst.name.isEmpty ? inst.id : inst.name
    }

    @ViewBuilder
    private var content: some View {
        switch model.state {
        case .loading:
            InstanceDetailSkeleton()
        case .failed:
            ScrollView {
                ErrorRow(message: model.state.errorMessage ?? "") {
                    Task { await model.reload() }
                }
                .padding(24)
            }
        case .loaded(let state):
            if let inst = state.instance {
                detail(inst, state)
            } else {
                ErrorRow(message: "Instance not found")
                    .padding(24)
            }
        }
    }

    private func detail(_ inst: Instance, _ state: InstanceDetailState) -> some View {
        let lanes = swingLanesOf(inst.strategy)
        return ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                header(inst)
                    .padding(.bottom, 8)
                if let message = state.errorMessage {
                    ErrorRow(message: message)
                }
                infoCard(inst, state)
                brokerageCard(inst)
                strategyCard(inst)
                if lanes.any {
                    PendingSignalsSection(model: signals) { toast = $0 }
                }
                if lanes.wheel {
                    WheelCard(model: wheel)
                }
                Card(padding: 16) {
                    InstanceStocksBlock(
                        title: "Stocks (\(inst.stocks.count))",
                        stocks: inst.stocks,
                        titleFont: .subheadline.weight(.semibold),
                        onAdd: { sheet = .addStock },
                        onRemove: { sym in
                            Task {
                                do { try await model.removeStock(sym) } catch {
                                    if !tradingIsCancellation(error) { toast = Toast(swingErrorText(error), style: .error) }
                                }
                            }
                        }
                    )
                }
                LiveLogsPanel(instanceId: inst.id)
                InstanceBacktestsSection(
                    state: state,
                    onNew: { sheet = .createBacktest },
                    onSort: { field in Task { await model.sortBacktests(field) } },
                    onPage: { page in Task { await model.goToBacktestPage(page) } },
                    onOpen: { services.router.push(.backtest($0)) }
                )
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .padding(.bottom, 80)
        }
        .refreshable {
            await model.refreshInstance()
            if lanes.any { await signals.build() }
            if lanes.wheel { await wheel.load() }
        }
    }

    // MARK: Header

    private func header(_ inst: Instance) -> some View {
        let isAi = inst.createdBy == "ai"
        return VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                IconTile(systemImage: Symbol.named("memory"), size: 44)
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(inst.name.isEmpty ? inst.id : inst.name)
                            .font(.title3.bold())
                            .lineLimit(1)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        AppBadge(label: isAi ? "AI" : "User", color: isAi ? DS.Palette.accent : .secondary)
                    }
                    Text(inst.id)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            HStack(spacing: 8) {
                InstanceStatusBadge(inst: inst)
                Button {
                    toggling = true
                    Task {
                        await model.toggleRun()
                        toggling = false
                    }
                } label: {
                    HStack(spacing: 4) {
                        if toggling {
                            ProgressView().controlSize(.mini)
                        } else {
                            Image(systemName: Symbol.named(inst.runCommand ? "stop" : "play_arrow"))
                        }
                        Text(inst.runCommand ? "Stop" : "Start")
                    }
                }
                .buttonStyle(.bordered)
                .tint(inst.runCommand ? DS.Palette.warning : DS.Palette.success)
                .disabled(toggling)
                Button {
                    services.router.push(.liveTrading(inst.id))
                } label: {
                    Label("Live Trading", systemImage: Symbol.named("monitoring"))
                }
                .buttonStyle(.bordered)
            }
            .controlSize(.small)
        }
    }

    // MARK: Cards

    private func cardTitle(_ text: String) -> some View {
        Text(text)
            .font(.subheadline.weight(.semibold))
            .accessibilityAddTraits(.isHeader)
    }

    private func tinyButton(_ label: String, _ symbol: String, _ tint: Color, role: ButtonRole? = nil, action: @escaping () -> Void) -> some View {
        Button(role: role, action: action) {
            Label(label, systemImage: Symbol.named(symbol))
                .font(.caption.weight(.semibold))
        }
        .buttonStyle(.borderless)
        .tint(tint)
        .frame(minHeight: 44)
    }

    private func infoCard(_ inst: Instance, _ state: InstanceDetailState) -> some View {
        Card(padding: 16) {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    cardTitle("Instance Info")
                    Spacer()
                    tinyButton("Clear State", "delete_sweep", DS.Palette.danger) { sheet = .clearState }
                }
                LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)], spacing: 8) {
                    StatTile(
                        label: "Uptime",
                        value: instanceUptimeLabel(state.liveUptimeSecs),
                        valueColor: inst.runCommand ? DS.Palette.success : .secondary
                    )
                    StatTile(label: "Granularity", value: instanceGranLabel(inst.granularityTimeIncrement))
                    if let maxUsage = inst.maxUsage {
                        StatTile(label: "Max Usage", value: fmtMoney(maxUsage))
                    }
                    StatTile(label: "Created By", value: inst.createdBy == "ai" ? "AI" : "User")
                }
            }
        }
    }

    private func brokerageCard(_ inst: Instance) -> some View {
        var name = "—"
        var type = ""
        if let b = inst.brokerage {
            name = (b["account_name"] ?? .null).string ?? "—"
            type = (b["brokerage_type"] ?? .null).string ?? ""
        } else if let id = inst.brokerageId {
            name = id
        }
        let linked = inst.brokerageId != nil
        return Card(padding: 16) {
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    cardTitle("Brokerage")
                    Spacer()
                    tinyButton(linked ? "Change" : "Link", "link", DS.Palette.teal) {
                        sheet = .linkBrokerage(inst.brokerageId)
                    }
                }
                .padding(.bottom, 8)
                InstanceInfoRow(label: "Trading Account", value: "\(name)\(type.isEmpty ? "" : " (\(type))")")
                InstanceInfoRow(label: "Market Data Source", value: inst.alpacaDataBrokerageId ?? "—")
                if linked {
                    tinyButton("Unlink Brokerage", "close", DS.Palette.danger, role: .destructive) {
                        confirm = ConfirmRequest(
                            title: "Unlink Brokerage",
                            body: "Remove the brokerage from this instance?",
                            confirmLabel: "Unlink",
                            role: .destructive,
                            onConfirm: { try await model.unlinkBrokerage() },
                            onError: { toast = Toast(swingErrorText($0), style: .error) }
                        )
                    }
                    .disabled(confirmRunning)
                    .padding(.top, 4)
                }
            }
        }
    }

    private func strategyCard(_ inst: Instance) -> some View {
        let name = (inst.strategy?["name"]).flatMap { $0.isNull ? nil : $0.dartDescription } ?? inst.strategyId ?? "—"
        let subs = inst.strategy?["strategies"]?.array ?? []
        return Card(padding: 16) {
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    cardTitle("Strategy")
                    Spacer()
                    if inst.strategyId == nil {
                        tinyButton("Link", "link", DS.Palette.accent) { sheet = .linkStrategy }
                    } else {
                        tinyButton("Unlink", "close", DS.Palette.danger, role: .destructive) {
                            confirm = ConfirmRequest(
                                title: "Unlink Strategy",
                                body: "Remove the strategy from this instance?",
                                confirmLabel: "Unlink",
                                role: .destructive,
                                onConfirm: { try await model.unlinkStrategy() },
                                onError: { toast = Toast(swingErrorText($0), style: .error) }
                            )
                        }
                        .disabled(confirmRunning)
                    }
                }
                .padding(.bottom, 8)
                if inst.strategyId == nil {
                    Text("No strategy linked")
                        .font(.footnote)
                        .italic()
                        .foregroundStyle(.secondary)
                } else {
                    InstanceInfoRow(label: "Name", value: name)
                    InstanceInfoRow(label: "ID", value: inst.strategyId ?? "—")
                    if !subs.isEmpty {
                        Text("Sub-strategies")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                            .padding(.top, 8)
                        ForEach(Array(subs.prefix(5).enumerated()), id: \.offset) { _, sub in
                            if sub.isObject {
                                HStack(spacing: 6) {
                                    Circle()
                                        .fill(DS.Palette.accent)
                                        .frame(width: 4, height: 4)
                                    Text(sub["strategy"].isNull ? "?" : sub["strategy"].dartDescription)
                                        .font(.footnote)
                                        .lineLimit(1)
                                }
                            }
                        }
                        if subs.count > 5 {
                            Text("+\(subs.count - 5) more")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
    }

    // MARK: Sheets

    @ViewBuilder
    private func sheetView(_ which: InstanceDetailSheet) -> some View {
        switch which {
        case .clearState:
            InstanceClearStateSheet(instanceId: instanceId, model: model)
        case .addStock:
            InstanceAddStockSheet { try await model.addStock($0) }
        case .linkBrokerage(let current):
            InstanceLinkSheet(kind: .brokerage, currentId: current) { try await model.linkBrokerage($0) }
        case .linkStrategy:
            InstanceLinkSheet(kind: .strategy) { try await model.linkStrategy($0) }
        case .createBacktest:
            InstanceCreateBacktestSheet(model: model)
        }
    }
}

/// `Label:` (120 pt) and value (`_InfoRow`).
private struct InstanceInfoRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 0) {
            Text("\(label):")
                .foregroundStyle(.secondary)
                .frame(width: 120, alignment: .leading)
            Text(value)
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .font(.footnote)
        .padding(.bottom, 4)
        .accessibilityElement(children: .combine)
    }
}

/// The instance's backtests: sort, rows with progress, pagination
/// (`_BacktestsSection`).
private struct InstanceBacktestsSection: View {
    let state: InstanceDetailState
    let onNew: () -> Void
    let onSort: (String) -> Void
    let onPage: (Int) -> Void
    let onOpen: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Backtests")
                    .font(.headline)
                    .accessibilityAddTraits(.isHeader)
                Spacer()
                Button(action: onNew) {
                    Label("New Backtest", systemImage: Symbol.named("play_circle"))
                        .font(.caption.weight(.semibold))
                }
                .buttonStyle(.borderless)
                .tint(DS.Palette.info)
                .frame(minHeight: 44)
            }
            if state.btLoading, state.backtests.isEmpty {
                ForEach(0..<3, id: \.self) { _ in
                    Skeleton(height: 60, radius: DS.Radius.control)
                }
            } else if state.backtests.isEmpty {
                Card(padding: 24) {
                    Text("No backtests yet")
                        .font(.footnote)
                        .italic()
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity)
                }
            } else {
                HStack(spacing: 8) {
                    sortButton("Date", "completed_at")
                    sortButton("PnL", "pnl")
                }
                ForEach(state.backtests) { bt in
                    InstanceBacktestCard(bt: bt, progress: state.btProgress[bt.id]) { onOpen(bt.id) }
                }
                if state.btTotalPages > 1 {
                    HStack {
                        Button {
                            onPage(state.btPage - 1)
                        } label: {
                            Image(systemName: Symbol.named("arrow_back"))
                                .frame(width: 44, height: 44)
                        }
                        .disabled(state.btPage <= 1)
                        .accessibilityLabel("Previous page")
                        Text("Page \(state.btPage) of \(state.btTotalPages)")
                            .font(.footnote)
                        Button {
                            onPage(state.btPage + 1)
                        } label: {
                            Image(systemName: Symbol.named("arrow_forward"))
                                .frame(width: 44, height: 44)
                        }
                        .disabled(state.btPage >= state.btTotalPages)
                        .accessibilityLabel("Next page")
                    }
                    .frame(maxWidth: .infinity)
                }
            }
        }
    }

    private func sortButton(_ label: String, _ field: String) -> some View {
        let active = state.btSortBy == field
        return Button {
            onSort(field)
        } label: {
            HStack(spacing: 2) {
                Text(label)
                Image(systemName: Symbol.named(active ? (state.btSortOrder == "asc" ? "arrow_upward" : "arrow_downward") : "unfold_more"))
                    .font(.caption2)
            }
            .font(.caption.weight(.semibold))
            .foregroundStyle(active ? AnyShapeStyle(.tint) : AnyShapeStyle(.secondary))
        }
        .buttonStyle(.borderless)
        .frame(minHeight: 44)
        .accessibilityAddTraits(active ? .isSelected : [])
    }
}

/// One backtest row (`_BacktestCard`).
private struct InstanceBacktestCard: View {
    let bt: InstanceBacktestRow
    let progress: Int?
    let onTap: () -> Void

    var body: some View {
        let running = instanceBacktestIsRunning(bt.status)
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text(bt.stocks.isEmpty ? "(no stocks)" : bt.stocks.joined(separator: ", "))
                        .font(.footnote)
                        .lineLimit(1)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    StatusBadge(label: bt.status, color: StatusBadge.color(forStatus: bt.status), pulsing: running)
                }
                HStack {
                    if let start = bt.startDate {
                        Text("\(start) → \(bt.endDate ?? "?")")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    } else {
                        Spacer()
                    }
                    if let pnl = bt.pnl {
                        Text(fmtPnl(pnl))
                            .font(.caption.monospaced().weight(.semibold))
                            .foregroundStyle(pnlColor(pnl))
                    }
                }
                if running, let progress {
                    ProgressView(value: min(max(Double(progress) / 100, 0), 1))
                        .tint(DS.Palette.info)
                    Text("\(progress)%")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                if let completed = bt.completedAt {
                    Text("Completed: \(fmtDateTime(completed))")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens the backtest")
    }
}

/// The detail screen's loading shape.
private struct InstanceDetailSkeleton: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 12) {
                    Skeleton.circle(44)
                    VStack(alignment: .leading, spacing: 6) {
                        Skeleton(height: 22, radius: 7)
                        Skeleton(width: 120, height: 10, radius: 5)
                    }
                }
                Skeleton(width: 240, height: 32, radius: 8)
                ForEach(0..<4, id: \.self) { _ in
                    Skeleton(height: 96, radius: DS.Radius.card)
                }
            }
            .padding(16)
        }
        .scrollDisabled(true)
        .accessibilityLabel("Loading")
    }
}
