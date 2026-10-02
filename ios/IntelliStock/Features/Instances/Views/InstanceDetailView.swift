import SwiftUI

/// One instance — `InstanceDetailScreen` in `instance_detail_screen.dart` —
/// as an inset-grouped list (redesign spec 2026-10-02). The instance's name
/// is the inline title; Start / Stop is the toolbar's primary action and the
/// rest (Live Trading, Clear State, the brokerage and strategy links) sits in
/// its More menu. Sections: status, brokerage, strategy, the swing and wheel
/// lanes, stocks, live logs and the instance's backtests.
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
    @State private var signalActions = PendingSignalsActions()
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

    private var instance: Instance? { model.value?.instance }
    private var lanes: SwingLanes { swingLanesOf(instance?.strategy) }

    var body: some View {
        @Bindable var signalActions = signalActions
        content
            .listStyle(.insetGrouped)
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { toolbar }
            .task { if model.value == nil { await model.load() } }
            .task { await model.runUptimeTicker() }
            .task { await model.runProgressPoll(lifecycle: services.lifecycle) }
            // The lanes' polls run for the screen, not for a list row.
            .task(id: lanes.any) {
                if lanes.any { await signals.poll(lifecycle: services.lifecycle) }
            }
            .task(id: lanes.wheel) {
                if lanes.wheel, wheel.state.value == nil { await wheel.load() }
            }
            .sheet(item: $sheet) { sheetView($0) }
            .confirmAlert($confirm, isRunning: $confirmRunning)
            .confirmAlert($signalActions.confirm, isRunning: $signalActions.confirmRunning)
            .sheet(item: $signalActions.review) { review in
                SwingOrderReviewSheet(review: review, cash: wheel.state.value?.cash)
            }
            .toast($toast)
    }

    private var title: String {
        guard let inst = instance else { return "" }
        return inst.name.isEmpty ? inst.id : inst.name
    }

    // MARK: Toolbar

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        if let inst = instance {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    toggling = true
                    Task {
                        await model.toggleRun()
                        toggling = false
                    }
                } label: {
                    if toggling {
                        ProgressView()
                    } else {
                        Text(inst.runCommand ? "Stop" : "Start")
                    }
                }
                .dsProminentButton()
                .disabled(toggling)
                .accessibilityLabel(inst.runCommand ? "Stop" : "Start")
            }
            ToolbarItem(placement: .topBarTrailing) {
                ToolbarMenu {
                    Section {
                        Button("Live Trading", systemImage: "chart.xyaxis.line") {
                            services.router.push(.liveTrading(inst.id))
                        }
                        Button("New Backtest", systemImage: "play.circle") { sheet = .createBacktest }
                    }
                    Section {
                        Button(inst.brokerageId != nil ? "Change Brokerage" : "Link Brokerage", systemImage: "link") {
                            sheet = .linkBrokerage(inst.brokerageId)
                        }
                        if inst.strategyId == nil {
                            Button("Link Strategy", systemImage: "link") { sheet = .linkStrategy }
                        }
                        Button("Add Stock…", systemImage: "plus") { sheet = .addStock }
                        Button("Copy ID", systemImage: "doc.on.doc") { UIPasteboard.general.string = inst.id }
                    }
                    Section {
                        Button("Clear State", systemImage: "eraser", role: .destructive) { sheet = .clearState }
                        if inst.brokerageId != nil {
                            Button("Unlink Brokerage", systemImage: "xmark", role: .destructive) { confirmUnlinkBrokerage() }
                                .disabled(confirmRunning)
                        }
                        if inst.strategyId != nil {
                            Button("Unlink Strategy", systemImage: "xmark", role: .destructive) { confirmUnlinkStrategy() }
                                .disabled(confirmRunning)
                        }
                    }
                    Section {
                        Button("Delete Instance", systemImage: "trash", role: .destructive) { confirmDelete(inst) }
                            .disabled(confirmRunning || services.instances.isBusy(inst.id))
                    }
                }
            }
        }
    }

    /// The list's own confirmation and delete (`instanceDeleteRequest`,
    /// `InstancesModel.delete` on the shared list). Once the instance is
    /// gone, back to the list, which the delete has already refetched; a
    /// failure stays here as an error toast.
    private func confirmDelete(_ inst: Instance) {
        confirm = instanceDeleteRequest(
            inst,
            onConfirm: {
                if case .failure(let error) = await services.instances.delete(inst.id) { throw error }
                let router = services.router
                if router.stack(for: router.tab).last == .instance(inst.id) {
                    router.pop()
                } else {
                    router.go("/instances")
                }
            },
            onError: { toast = Toast(swingErrorText($0), style: .error) }
        )
    }

    // MARK: Content

    @ViewBuilder
    private var content: some View {
        switch model.state {
        case .loading:
            InstanceDetailSkeleton()
        case .failed:
            List {
                Section {
                    ErrorRow(message: model.state.errorMessage ?? "") {
                        Task { await model.reload() }
                    }
                }
            }
        case .loaded(let state):
            if let inst = state.instance {
                detail(inst, state)
            } else {
                List {
                    Section { ErrorRow(message: "Instance not found") }
                }
            }
        }
    }

    private func detail(_ inst: Instance, _ state: InstanceDetailState) -> some View {
        let lanes = swingLanesOf(inst.strategy)
        let pendingIds = Set(signals.state.value?.signals.map(\.id) ?? [])
        return ScrollViewReader { proxy in
          List {
            if let message = state.errorMessage {
                Section { ErrorRow(message: message) }
            }
            heroSection(inst, state)
            // The review queue sits up top: it is what this screen is for on
            // a swing or wheel instance.
            if lanes.any {
                PendingSignalsSection(
                    model: signals, actions: signalActions, cash: wheel.state.value?.cash,
                    showToast: { toast = $0 },
                    onDecided: { Task { await wheel.loadSignals() } }
                )
            }
            if lanes.wheel {
                WheelSections(model: wheel, pendingIds: pendingIds) { id in
                    withAnimation { proxy.scrollTo(swingSignalAnchor(id), anchor: .top) }
                }
            }
            statusSection(inst, state)
            brokerageSection(inst)
            strategySection(inst)
            stocksSection(inst)
            Section("Live logs") {
                LiveLogsPanel(instanceId: inst.id)
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
            }
            InstanceBacktestsSection(
                state: state,
                onNew: { sheet = .createBacktest },
                onSort: { field in Task { await model.sortBacktests(field) } },
                onPage: { page in Task { await model.goToBacktestPage(page) } }
            )
          }
        }
        .refreshable {
            await model.refreshInstance()
            if lanes.any { await signals.build() }
            if lanes.wheel { await wheel.load() }
        }
    }

    // MARK: Hero

    /// The header: the run state with its uptime, the strategy and the
    /// brokerage, then the account's cash when the wheel book has it.
    private func heroSection(_ inst: Instance, _ state: InstanceDetailState) -> some View {
        let strategy = (inst.strategy?["name"]).flatMap { $0.isNull ? nil : $0.dartDescription } ?? inst.strategyId
        let brokerage = (inst.brokerage?["account_name"] ?? .null).string
            ?? inst.brokerageId.map { instanceBrokerageName($0, nested: nil, brokerages: services.dashboard.brokeragesValue) }
        let subtitle = [strategy, brokerage].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
        let book = wheel.state.value
        return Section {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 10) {
                    InstanceStatusDot(inst: inst)
                    if inst.runCommand, state.liveUptimeSecs > 0 {
                        Text("Up \(instanceUptimeLabel(state.liveUptimeSecs))")
                            .font(.subheadline.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 0)
                    if case .bool(let paper)? = inst.brokerage?["alpaca_paper"] {
                        StatusBadge(label: paper ? "Paper" : "Live", color: paper ? DS.Palette.info : DS.Palette.danger)
                    }
                }
                if !subtitle.isEmpty {
                    Text(subtitle)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                if let cash = book?.cash {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(fmtMoney(cash))
                            .font(.largeTitle.weight(.bold))
                            .monospacedDigit()
                            .lineLimit(1)
                            .minimumScaleFactor(0.6)
                        Text("Cash available")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.top, 2)
                    .accessibilityElement(children: .combine)
                }
            }
            .padding(.vertical, 6)
        }
    }

    // MARK: Status

    private func statusSection(_ inst: Instance, _ state: InstanceDetailState) -> some View {
        Section("Details") {
            StatGrid(columns: 2) {
                StatCell(
                    label: "Uptime",
                    value: instanceUptimeLabel(state.liveUptimeSecs),
                    valueColor: inst.runCommand ? DS.Palette.success : .secondary
                )
                StatCell(label: "Granularity", value: instanceGranLabel(inst.granularityTimeIncrement))
                if let maxUsage = inst.maxUsage {
                    StatCell(label: "Max usage", value: fmtMoney(maxUsage))
                }
                StatCell(label: "Created by", value: inst.createdBy == "ai" ? "AI" : "User")
            }
            .padding(.vertical, 4)
            LabeledContent("ID") {
                Text(inst.id)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            .contextMenu {
                Button("Copy ID", systemImage: "doc.on.doc") { UIPasteboard.general.string = inst.id }
            }
        }
    }

    // MARK: Brokerage

    private func brokerageSection(_ inst: Instance) -> some View {
        var name = "—"
        var type = ""
        if let b = inst.brokerage {
            name = (b["account_name"] ?? .null).string ?? "—"
            type = (b["brokerage_type"] ?? .null).string ?? ""
        } else if let id = inst.brokerageId {
            name = instanceBrokerageName(id, nested: nil, brokerages: services.dashboard.brokeragesValue)
        }
        let dataSource = inst.alpacaDataBrokerageId.map {
            instanceBrokerageName($0, nested: nil, brokerages: services.dashboard.brokeragesValue)
        } ?? "—"
        let linked = inst.brokerageId != nil
        return Section("Brokerage") {
            LabeledContent("Trading account", value: "\(name)\(type.isEmpty ? "" : " (\(type))")")
            LabeledContent("Market data source", value: dataSource)
            if !linked {
                InlineActionRow("Link Brokerage", systemImage: "link") { sheet = .linkBrokerage(inst.brokerageId) }
            }
        }
    }

    private func confirmUnlinkBrokerage() {
        confirm = ConfirmRequest(
            title: "Unlink Brokerage",
            body: "Remove the brokerage from this instance?",
            confirmLabel: "Unlink",
            role: .destructive,
            onConfirm: { try await model.unlinkBrokerage() },
            onError: { toast = Toast(swingErrorText($0), style: .error) }
        )
    }

    // MARK: Strategy

    private func strategySection(_ inst: Instance) -> some View {
        let name = (inst.strategy?["name"]).flatMap { $0.isNull ? nil : $0.dartDescription } ?? inst.strategyId ?? "—"
        let subs = inst.strategy?["strategies"]?.array ?? []
        return Section("Strategy") {
            if inst.strategyId == nil {
                Text("No strategy linked")
                    .foregroundStyle(.secondary)
                InlineActionRow("Link Strategy", systemImage: "link") { sheet = .linkStrategy }
            } else {
                LabeledContent("Name", value: name)
                LabeledContent("ID", value: inst.strategyId ?? "—")
                if !subs.isEmpty {
                    LabeledContent("Sub-strategies") {
                        VStack(alignment: .trailing, spacing: 2) {
                            ForEach(Array(subs.prefix(5).enumerated()), id: \.offset) { _, sub in
                                if sub.isObject {
                                    Text(sub["strategy"].isNull ? "?" : sub["strategy"].dartDescription)
                                        .lineLimit(1)
                                }
                            }
                            if subs.count > 5 {
                                Text("+\(subs.count - 5) more")
                                    .font(.footnote)
                            }
                        }
                    }
                }
            }
        }
    }

    private func confirmUnlinkStrategy() {
        confirm = ConfirmRequest(
            title: "Unlink Strategy",
            body: "Remove the strategy from this instance?",
            confirmLabel: "Unlink",
            role: .destructive,
            onConfirm: { try await model.unlinkStrategy() },
            onError: { toast = Toast(swingErrorText($0), style: .error) }
        )
    }

    // MARK: Stocks

    private func stocksSection(_ inst: Instance) -> some View {
        DSSection("Stocks (\(inst.stocks.count))", action: DSSectionAction("Add", systemImage: "plus") { sheet = .addStock }) {
            if inst.stocks.isEmpty {
                Text("No stocks added")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(inst.stocks, id: \.self) { sym in
                    Text(sym)
                        .font(.headline)
                        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                            Button("Remove", systemImage: "trash", role: .destructive) { removeStock(sym) }
                        }
                        .contextMenu {
                            Button("Remove", systemImage: "trash", role: .destructive) { removeStock(sym) }
                        }
                        .accessibilityHint("Swipe left to remove")
                }
            }
        }
    }

    private func removeStock(_ symbol: String) {
        Task {
            do { try await model.removeStock(symbol) } catch {
                if !error.isCancellationOrTaskCancelled { toast = Toast(swingErrorText(error), style: .error) }
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

/// The instance's backtests (`_BacktestsSection`): sort and New Backtest in
/// the header, a row per backtest that opens it, and paging.
private struct InstanceBacktestsSection: View {
    let state: InstanceDetailState
    let onNew: () -> Void
    let onSort: (String) -> Void
    let onPage: (Int) -> Void

    var body: some View {
        Section {
            if state.btLoading, state.backtests.isEmpty {
                DashboardPlaceholderRows(count: 3)
            } else if state.backtests.isEmpty {
                Text("No backtests yet")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(state.backtests) { bt in
                    NavigationLink(value: Route.backtest(bt.id)) {
                        InstanceBacktestRowView(bt: bt, progress: state.btProgress[bt.id])
                    }
                }
                if state.btTotalPages > 1 {
                    HStack {
                        Button {
                            onPage(state.btPage - 1)
                        } label: {
                            Image(systemName: "chevron.backward")
                                .frame(width: 44, height: 44)
                        }
                        .disabled(state.btPage <= 1)
                        .accessibilityLabel("Previous page")
                        Spacer()
                        Text("Page \(state.btPage) of \(state.btTotalPages)")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                        Spacer()
                        Button {
                            onPage(state.btPage + 1)
                        } label: {
                            Image(systemName: "chevron.forward")
                                .frame(width: 44, height: 44)
                        }
                        .disabled(state.btPage >= state.btTotalPages)
                        .accessibilityLabel("Next page")
                    }
                    .buttonStyle(.borderless)
                }
            }
        } header: {
            DSSectionHeader("Backtests") {
                HStack(spacing: 18) {
                    if !state.backtests.isEmpty {
                        Menu {
                            sortButton("Date", "completed_at")
                            sortButton("PnL", "pnl")
                        } label: {
                            Label("Sort", systemImage: "arrow.up.arrow.down")
                                .labelStyle(.iconOnly)
                        }
                        .accessibilityLabel("Sort")
                    }
                    Button(action: onNew) {
                        Label("New Backtest", systemImage: "plus")
                            .labelStyle(.iconOnly)
                    }
                    .accessibilityLabel("New Backtest")
                }
            }
        }
    }

    /// One sort field: choosing the active field again flips its order.
    private func sortButton(_ label: String, _ field: String) -> some View {
        let active = state.btSortBy == field
        return Button {
            onSort(field)
        } label: {
            if active {
                Label(label, systemImage: state.btSortOrder == "asc" ? "arrow.up" : "arrow.down")
            } else {
                Text(label)
            }
        }
    }
}

/// One backtest row (`_BacktestCard`): the stocks, the date range, the P&L,
/// the status when it is not finished, and progress while it runs.
private struct InstanceBacktestRowView: View {
    let bt: InstanceBacktestRow
    let progress: Int?

    var body: some View {
        let running = instanceBacktestIsRunning(bt.status)
        let finished = ["finished", "completed"].contains(bt.status.lowercased())
        VStack(alignment: .leading, spacing: 6) {
            EntityRow(
                bt.stocks.isEmpty ? "(no stocks)" : bt.stocks.joined(separator: ", "),
                subtitle: bt.startDate.map { "\($0) → \(bt.endDate ?? "?")" }
            ) {
                VStack(alignment: .trailing, spacing: 2) {
                    if let pnl = bt.pnl {
                        Text(fmtPnl(pnl))
                            .font(.body.monospacedDigit())
                            .foregroundStyle(pnlColor(pnl))
                    }
                    if !finished {
                        StatusDot(bt.status.dsSentenceCased, status: bt.status, pulsing: running, font: .footnote)
                    }
                }
            }
            if running, let progress {
                ProgressView(value: min(max(Double(progress) / 100, 0), 1)) {
                    EmptyView()
                } currentValueLabel: {
                    Text("\(progress)%")
                }
                .tint(DS.Palette.info)
            }
            if let completed = bt.completedAt {
                Text("Completed: \(fmtDateTime(completed))")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens the backtest")
    }
}

/// The detail screen's loading shape.
private struct InstanceDetailSkeleton: View {
    var body: some View {
        List {
            Section("Status") {
                StatGrid(columns: 3) {
                    StatCell(label: "Status", value: "Running")
                    StatCell(label: "Uptime", value: "00h 00m")
                    StatCell(label: "Granularity", value: "1d")
                }
            }
            Section("Brokerage") {
                LabeledContent("Trading account", value: "Account name")
                LabeledContent("Market data source", value: "Account name")
            }
            Section("Strategy") {
                LabeledContent("Name", value: "Strategy name")
                LabeledContent("ID", value: "000")
            }
        }
        .redacted(reason: .placeholder)
        .disabled(true)
        .accessibilityLabel("Loading")
    }
}
