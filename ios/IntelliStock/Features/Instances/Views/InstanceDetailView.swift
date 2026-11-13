import SwiftUI

/// One instance — `InstanceDetailScreen` in `instance_detail_screen.dart` —
/// styled as the web's `InstanceDetailView.vue`. The instance's name is the
/// title; Start / Stop is the toolbar's primary action and the rest (Live
/// trading, Clear data, the brokerage and strategy links) sits in its More
/// menu. Under the title: the id, origin and run state, then the linked
/// account's value today. Sections, in the web's order: Instance info,
/// Brokerage, Strategy, Stocks, Backtests — with the swing review queue and
/// wheel book up top on a swing instance, and the live logs before the
/// backtests.
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

    /// The linked account, for the hero's equity (the list's shared cache).
    private var account: BrokerageAccount? {
        guard let inst = instance else { return nil }
        return instanceRowAccounts([inst], brokerages: services.dashboard.brokeragesValue).first
    }

    var body: some View {
        @Bindable var signalActions = signalActions
        content
            .dsList()
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.large)
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
            .task(id: account?.id) {
                if let account { await services.instanceRows.refreshAccounts([account]) }
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
                .disabled(toggling)
                .accessibilityLabel(inst.runCommand ? "Stop" : "Start")
            }
            ToolbarItem(placement: .topBarTrailing) {
                ToolbarMenu {
                    Section {
                        Button("Live trading", systemImage: "chart.xyaxis.line") {
                            services.router.push(.liveTrading(inst.id))
                        }
                        Button("New backtest", systemImage: "play.circle") { sheet = .createBacktest }
                    }
                    Section {
                        Button(inst.brokerageId != nil ? "Change brokerage" : "Link brokerage", systemImage: "link") {
                            sheet = .linkBrokerage(inst.brokerageId)
                        }
                        if inst.strategyId == nil {
                            Button("Link strategy", systemImage: "link") { sheet = .linkStrategy }
                        }
                        Button("Add stock", systemImage: "plus") { sheet = .addStock }
                        Button("Copy ID", systemImage: "doc.on.doc") { UIPasteboard.general.string = inst.id }
                    }
                    Section {
                        Button("Clear data", systemImage: "eraser", role: .destructive) { sheet = .clearState }
                        if inst.brokerageId != nil {
                            Button("Unlink brokerage", systemImage: "xmark", role: .destructive) { confirmUnlinkBrokerage() }
                                .disabled(confirmRunning)
                        }
                        if inst.strategyId != nil {
                            Button("Unlink strategy", systemImage: "xmark", role: .destructive) { confirmUnlinkStrategy() }
                                .disabled(confirmRunning)
                        }
                    }
                    Section {
                        Button("Delete instance", systemImage: "trash", role: .destructive) { confirmDelete(inst) }
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
                    .dsClearRow(top: 8)
                }
            }
        case .loaded(let state):
            if let inst = state.instance {
                detail(inst, state)
            } else {
                List {
                    Section { ErrorRow(message: "Instance not found").dsClearRow(top: 8) }
                }
            }
        }
    }

    private func detail(_ inst: Instance, _ state: InstanceDetailState) -> some View {
        let lanes = swingLanesOf(inst.strategy)
        let pendingIds = Set(signals.state.value?.signals.map(\.id) ?? [])
        return ScrollViewReader { proxy in
          List {
            heroSection(inst, state)
            if let message = state.errorMessage {
                Section { ErrorRow(message: message).dsClearRow() }
            }
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
                WheelSections(
                    model: wheel,
                    pendingIds: pendingIds,
                    brokerageId: inst.brokerageId,
                    portfolioTotal: inst.brokerageId.flatMap { services.instanceRows.accounts.summary($0)?.equity }
                ) { id in
                    withAnimation(DSMotion.ease(0.3)) { proxy.scrollTo(swingSignalAnchor(id), anchor: .top) }
                }
            }
            infoSection(inst, state)
            brokerageSection(inst)
            strategySection(inst)
            stocksSection(inst)
            // The panel carries its own "Live logs" heading.
            DSSection(style: .plain) {
                LiveLogsPanel(instanceId: inst.id)
                    .dsClearRow()
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
            if let account { await services.instanceRows.refreshAccounts([account], force: true) }
            if lanes.any { await signals.build() }
            if lanes.wheel { await wheel.load() }
        }
    }

    // MARK: Hero

    /// Under the title: "alpaca-main · Created by user · ● Running" (the
    /// web's page subline), then the linked account's value today with its
    /// change, and the wheel book's cash as a secondary figure.
    private func heroSection(_ inst: Instance, _ state: InstanceDetailState) -> some View {
        let accounts = services.instanceRows.accounts
        let brokerages = services.dashboard.brokeragesValue
        let summary = inst.brokerageId.flatMap { accounts.summary($0) }
        let pending = summary == nil && (inst.brokerageId.map { accounts.isPending($0) } ?? false)
        let cash = wheel.state.value?.cash
        return Section {
            VStack(alignment: .leading, spacing: 0) {
                subline(inst)
                if let bid = inst.brokerageId, summary != nil || pending {
                    accountLabel(inst, bid, brokerages: brokerages)
                        .padding(.top, 24)
                        .padding(.bottom, 4)
                    if let summary {
                        HeroValueHeader(
                            fmtMoney(summary.equity),
                            numericValue: summary.equity,
                            change: summary.dayChange == nil ? nil : summary.changeText,
                            direction: summary.direction,
                            changeLabel: "Today"
                        ) {
                            if let cash {
                                Text("Cash available \(fmtMoney(cash))")
                                    .monospacedDigit()
                            }
                        }
                    } else {
                        VStack(alignment: .leading, spacing: 12) {
                            Skeleton(width: 220, height: 40, radius: DS.Radius.small)
                            Skeleton(width: 150, height: 14)
                        }
                        .accessibilityLabel("Loading")
                    }
                }
            }
            .dsClearRow(top: 0, bottom: 8)
        }
    }

    /// "alpaca-main · Created by user · ● Running".
    private func subline(_ inst: Instance) -> some View {
        let state = InstanceRunState(inst)
        return HStack(spacing: 8) {
            Text(inst.id)
                .lineLimit(1)
                .truncationMode(.middle)
            Text("·").foregroundStyle(DS.Text.faint)
            Text(inst.createdBy == "ai" ? "Created by AI" : "Created by user")
                .lineLimit(1)
            Text("·").foregroundStyle(DS.Text.faint)
            StatusDot(state.word, tone: state.tone, pulsing: state == .running, font: DS.Font.body)
        }
        .font(DS.Font.body)
        .foregroundStyle(DS.Text.muted)
        .monospacedDigit()
        .accessibilityElement(children: .combine)
    }

    /// "Alpaca Live · Paper", or "Alpaca Live · Live money" with the warning
    /// word in amber.
    private func accountLabel(_ inst: Instance, _ bid: String, brokerages: [BrokerageAccount]?) -> some View {
        let name = instanceBrokerageName(bid, nested: inst.brokerage, brokerages: brokerages)
        let paper = instanceIsPaper(inst, brokerages: brokerages)
        var line = Text(name).foregroundStyle(DS.Text.muted)
        if let paper {
            line = line + Text(" · ").foregroundStyle(DS.Text.faint)
                + (paper ? Text("Paper").foregroundStyle(DS.Text.muted) : Text("Live money").foregroundStyle(DS.Palette.amber))
        }
        return line
            .font(DS.Font.caption)
            .lineLimit(1)
    }

    // MARK: Instance info

    private func infoSection(_ inst: Instance, _ state: InstanceDetailState) -> some View {
        DSSection("Instance info", rowMinHeight: 44) {
            DSKeyValueRow("Granularity", instanceGranLabel(inst.granularityTimeIncrement))
            DSKeyValueRow(
                "Uptime",
                instanceUptimeLabel(state.liveUptimeSecs),
                valueColor: inst.runCommand && state.liveUptimeSecs > 0 ? DS.Text.primary : DS.Text.muted
            )
            DSKeyValueRow("Created by", inst.createdBy == "ai" ? "AI" : "User")
            if let maxUsage = inst.maxUsage {
                DSKeyValueRow("Max usage", fmtMoney(maxUsage))
            }
            HStack(spacing: 16) {
                Text("ID")
                    .foregroundStyle(DS.Text.secondary)
                Spacer(minLength: 8)
                Text(inst.id)
                    .foregroundStyle(DS.Text.primary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            .font(DS.Font.body)
            .accessibilityElement(children: .combine)
            .contextMenu {
                Button("Copy ID", systemImage: "doc.on.doc") { UIPasteboard.general.string = inst.id }
            }
        } accessory: {
            Button("Clear data", role: .destructive) { sheet = .clearState }
                .buttonStyle(.dsSecondary)
        }
    }

    // MARK: Brokerage

    private func brokerageSection(_ inst: Instance) -> some View {
        let brokerages = services.dashboard.brokeragesValue
        var name = "—"
        var type = ""
        if let b = inst.brokerage {
            name = (b["account_name"] ?? .null).string ?? "—"
            type = (b["brokerage_type"] ?? .null).string ?? ""
        } else if let id = inst.brokerageId {
            name = instanceBrokerageName(id, nested: nil, brokerages: brokerages)
        }
        // The data account is often the trading account itself: name it
        // from the nested brokerage then.
        let dataSource = inst.alpacaDataBrokerageId.map {
            instanceBrokerageName($0, nested: $0 == inst.brokerageId ? inst.brokerage : nil, brokerages: brokerages)
        } ?? "—"
        let linked = inst.brokerageId != nil
        let paper = instanceIsPaper(inst, brokerages: brokerages)
        return DSSection("Brokerage", rowMinHeight: 44) {
            if linked {
                EntityRow(name, subtitle: type.isEmpty ? nil : type.dsSentenceCased)
                if let paper {
                    DSKeyValueRow("Mode", paper ? "Paper" : "Live")
                }
            } else {
                Text("No brokerage linked")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
            }
            DSKeyValueRow("Market data source", dataSource)
        } accessory: {
            HStack(spacing: 8) {
                if linked {
                    Button("Unlink", role: .destructive) { confirmUnlinkBrokerage() }
                        .buttonStyle(.dsSecondary)
                        .disabled(confirmRunning)
                }
                Button(linked ? "Change" : "Link") { sheet = .linkBrokerage(inst.brokerageId) }
                    .buttonStyle(.dsSecondary)
            }
        }
    }

    private func confirmUnlinkBrokerage() {
        confirm = ConfirmRequest(
            title: "Unlink brokerage",
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
        let subs = (inst.strategy?["strategies"]?.array ?? []).filter(\.isObject)
        let linked = inst.strategyId != nil
        return DSSection("Strategy", rowMinHeight: 44) {
            if !linked {
                Text("No strategy linked")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
            } else {
                EntityRow(name, subtitle: "ID \(inst.strategyId ?? "—")")
                ForEach(Array(subs.prefix(5).enumerated()), id: \.offset) { _, sub in
                    Text(sub["strategy"].isNull ? "?" : sub["strategy"].dartDescription)
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.secondary)
                        .lineLimit(1)
                }
                if subs.count > 5 {
                    Text("+\(subs.count - 5) more")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                }
            }
        } accessory: {
            if linked {
                Button("Unlink", role: .destructive) { confirmUnlinkStrategy() }
                    .buttonStyle(.dsSecondary)
                    .disabled(confirmRunning)
            } else {
                Button("Link") { sheet = .linkStrategy }
                    .buttonStyle(.dsSecondary)
            }
        }
    }

    private func confirmUnlinkStrategy() {
        confirm = ConfirmRequest(
            title: "Unlink strategy",
            body: "Remove the strategy from this instance?",
            confirmLabel: "Unlink",
            role: .destructive,
            onConfirm: { try await model.unlinkStrategy() },
            onError: { toast = Toast(swingErrorText($0), style: .error) }
        )
    }

    // MARK: Stocks

    private func stocksSection(_ inst: Instance) -> some View {
        let count = inst.stocks.count
        return DSSection("Stocks", subtitle: "\(count) symbol\(count == 1 ? "" : "s")", rowMinHeight: 44) {
            if inst.stocks.isEmpty {
                Text("No stocks added")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
            } else {
                ForEach(inst.stocks, id: \.self) { sym in
                    Text(sym)
                        .font(DS.Font.bodyMedium)
                        .foregroundStyle(DS.Text.primary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                            Button("Remove", systemImage: "trash", role: .destructive) { removeStock(sym) }
                        }
                        .contextMenu {
                            Button("Remove", systemImage: "trash", role: .destructive) { removeStock(sym) }
                        }
                        .accessibilityHint("Swipe left to remove")
                }
            }
        } accessory: {
            Button("Add stock") { sheet = .addStock }
                .buttonStyle(.dsSecondary)
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

/// The instance's backtests (the web's Backtests section): "N runs" under
/// the title, sort and New backtest beside it, a row per backtest that
/// opens it, and the pager.
private struct InstanceBacktestsSection: View {
    let state: InstanceDetailState
    let onNew: () -> Void
    let onSort: (String) -> Void
    let onPage: (Int) -> Void

    var body: some View {
        DSSection("Backtests", subtitle: "\(state.btTotal) \(state.btTotal == 1 ? "run" : "runs")") {
            if state.btLoading, state.backtests.isEmpty {
                ForEach(0..<4, id: \.self) { _ in
                    Skeleton(width: 192, height: 16)
                        .accessibilityHidden(true)
                }
            } else if state.backtests.isEmpty {
                EmptyState(title: "No backtests run for this instance yet")
            } else {
                ForEach(state.backtests) { bt in
                    NavigationLink(value: Route.backtest(bt.id)) {
                        InstanceBacktestRowView(bt: bt, progress: state.btProgress[bt.id])
                    }
                }
                if state.btTotalPages > 1 {
                    pager
                }
            }
        } accessory: {
            HStack(spacing: 4) {
                if !state.backtests.isEmpty {
                    Menu {
                        sortButton("Date completed", "completed_at")
                        sortButton("P&L", "pnl")
                    } label: {
                        Image(systemName: "arrow.up.arrow.down")
                            .font(.system(size: 16))
                            .foregroundStyle(DS.Text.muted)
                            .frame(width: 32, height: 32)
                            .contentShape(Rectangle())
                    }
                    .accessibilityLabel("Sort")
                }
                Button("New backtest", action: onNew)
                    .buttonStyle(.dsSecondary)
            }
        }
    }

    /// "Page 1 of 3 · 37 total" and the previous / next buttons.
    private var pager: some View {
        HStack(spacing: 4) {
            Text("Page \(state.btPage) of \(state.btTotalPages) · \(state.btTotal) total")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .monospacedDigit()
            Spacer()
            Button {
                onPage(state.btPage - 1)
            } label: {
                Image(systemName: "chevron.backward")
            }
            .buttonStyle(.dsIcon)
            .disabled(state.btPage <= 1)
            .accessibilityLabel("Previous page")
            Button {
                onPage(state.btPage + 1)
            } label: {
                Image(systemName: "chevron.forward")
            }
            .buttonStyle(.dsIcon)
            .disabled(state.btPage >= state.btTotalPages)
            .accessibilityLabel("Next page")
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

/// One backtest row (the web's table row, stacked for a phone): "#81204"
/// over its stocks, the P&L and P&L % on the right; then the period, and
/// the status with the completion date or the run's progress.
private struct InstanceBacktestRowView: View {
    let bt: InstanceBacktestRow
    let progress: Int?

    var body: some View {
        let running = instanceBacktestIsRunning(bt.status)
        VStack(alignment: .leading, spacing: 6) {
            EntityRow("#\(bt.id)", subtitle: stocksText) {
                EntityRowValue(
                    bt.pnl.map { fmtPnl($0) } ?? "—",
                    color: bt.pnl.map { pnlColor($0) } ?? DS.Text.muted,
                    detail: bt.pnlPercent.map { fmtPct($0) },
                    detailColor: bt.pnlPercent.map { pnlColor($0) }
                )
            }
            if let start = bt.startDate {
                Text("\(start) → \(bt.endDate ?? "?")")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.secondary)
                    .monospacedDigit()
            }
            HStack(spacing: 4) {
                StatusDot(bt.status.replacingOccurrences(of: "_", with: " ").dsSentenceCased, status: bt.status, pulsing: running, font: DS.Font.caption)
                if running, let progress {
                    // The web's 4 pt bar: zinc-300 on raised.
                    Capsule()
                        .fill(DS.Surface.raised)
                        .frame(width: 80, height: 4)
                        .overlay(alignment: .leading) {
                            Capsule()
                                .fill(DS.Text.label)
                                .frame(width: 80 * Double(min(max(progress, 0), 100)) / 100, height: 4)
                        }
                        .padding(.horizontal, 8)
                    Text("\(progress)%")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.secondary)
                        .monospacedDigit()
                } else if let completed = bt.completedAt {
                    Text("· \(fmtDateTime(completed))")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                        .lineLimit(1)
                }
            }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens the backtest")
    }

    /// The first four stocks, then "+N"; "—" for none (the web's).
    private var stocksText: String {
        guard !bt.stocks.isEmpty else { return "—" }
        let shown = bt.stocks.prefix(4).joined(separator: ", ")
        return bt.stocks.count > 4 ? "\(shown) +\(bt.stocks.count - 4)" : shown
    }
}

/// The detail screen's loading shape: static blocks where the subline, the
/// account value and the first sections will be.
private struct InstanceDetailSkeleton: View {
    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 12) {
                    Skeleton(width: 220, height: 14)
                    Skeleton(width: 120, height: 12)
                        .padding(.top, 16)
                    Skeleton(width: 220, height: 40, radius: DS.Radius.small)
                }
                .dsClearRow(top: 0, bottom: 8)
            }
            DSSection("Instance info", rowMinHeight: 44) {
                ForEach(0..<4, id: \.self) { _ in
                    HStack {
                        Skeleton(width: 96, height: 14)
                        Spacer()
                        Skeleton(width: 64, height: 14)
                    }
                }
            }
            DSSection("Brokerage", rowMinHeight: 44) {
                ForEach(0..<2, id: \.self) { _ in
                    HStack {
                        Skeleton(width: 120, height: 14)
                        Spacer()
                        Skeleton(width: 80, height: 14)
                    }
                }
            }
        }
        .disabled(true)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Loading")
    }
}
