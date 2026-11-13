import SwiftUI

/// The Instances tab root — `InstancesScreen` in `instances_screen.dart`,
/// styled as the web's `InstancesView.vue`: the subtitle under the large
/// title, the All / User created / AI created filter, the pinned instances
/// as cards ("Pinned"), then every other instance as a row ("Other
/// instances"). A card or row opens the instance; pin, start/stop, live
/// trading, add stock and delete sit in its swipe actions and context menu
/// (a card also carries the web's pin, Start / Stop and More controls).
struct InstancesView: View {
    @Environment(AppServices.self) private var services

    var body: some View {
        InstancesContent(services: services)
    }
}

/// The page subtitle (`ui-page-sub`).
private let instancesPageSub = "Live trading and backtesting bots"

private struct InstancesContent: View {
    let services: AppServices

    /// The shared list (`services.instances`): Instance detail's Delete
    /// Instance runs through it too.
    private var model: InstancesModel { services.instances }
    @State private var pinned = PinnedInstancesModel()
    @State private var showCreate = false
    @State private var addStockFor: InstanceSheetTarget?
    @State private var confirm: ConfirmRequest?
    /// A confirmed delete is running: every Delete stays disabled.
    @State private var confirmRunning = false
    @State private var toast: Toast?

    init(services: AppServices) {
        self.services = services
    }

    var body: some View {
        content
            .dsList()
            .navigationTitle("Instances")
            .navigationBarTitleDisplayMode(.large)
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    ToolbarAddButton("New instance") { showCreate = true }
                }
            }
            .task { await model.poll(lifecycle: services.lifecycle) }
            // The rows' extra figures: on appear and whenever the list's
            // instances or links change. Rows fill in as answers land.
            .task(id: rowsKey) {
                guard let list = model.value?.instances, !list.isEmpty else { return }
                services.instanceRows.refreshDetached(list, brokerages: services.dashboard.brokeragesValue)
            }
            .sheet(isPresented: $showCreate) {
                InstanceCreateSheet(model: model)
            }
            .sheet(item: $addStockFor) { target in
                InstanceAddStockSheet { symbol in
                    try await model.addStock(target.id, symbol)
                }
            }
            .confirmAlert($confirm, isRunning: $confirmRunning)
            .toast($toast)
    }

    /// What the rows' figures depend on: each instance and its links.
    private var rowsKey: [String] {
        (model.value?.instances ?? []).map { "\($0.id)|\($0.strategyId ?? "")|\($0.brokerageId ?? "")|\($0.runCommand)" }
    }

    @ViewBuilder
    private var content: some View {
        switch model.state {
        case .loading:
            InstancesSkeleton()
        case .failed:
            List {
                pageSub
                Section {
                    ErrorRow(message: model.state.errorMessage ?? "") {
                        Task { await model.reload() }
                    }
                    .dsClearRow(top: 24)
                }
            }
        case .loaded(let state):
            loadedBody(state)
        }
    }

    /// The subtitle under the large title, as the list's first row.
    private var pageSub: some View {
        Section {
            Text(instancesPageSub)
                .font(DS.Font.pageSub)
                .foregroundStyle(DS.Text.muted)
                .dsClearRow(top: 0)
        }
    }

    private func loadedBody(_ state: InstancesState) -> some View {
        let pinnedIds = pinned.pinned
        let pinnedItems = state.filtered.filter { pinnedIds.contains($0.id) }
        let others = state.filtered.filter { !pinnedIds.contains($0.id) }
        return List {
            pageSub
            Section {
                DSSegmentedControl(
                    "Filter instances",
                    selection: Binding(get: { state.filter }, set: { model.setFilter($0) }),
                    options: [InstanceFilter.all, .user, .ai]
                ) { filter in
                    switch filter {
                    case .all: "All \(state.allCount)"
                    case .user: "User created \(state.userCount)"
                    case .ai: "AI created \(state.aiCount)"
                    }
                }
                .dsClearRow(top: 16)
            }

            if state.instances.isEmpty {
                DSSection {
                    EmptyState(
                        title: "No instances yet",
                        subtitle: "Create one to run a strategy live or in a backtest",
                        actionLabel: "New instance",
                        onAction: { showCreate = true }
                    )
                }
            } else if state.filtered.isEmpty {
                DSSection {
                    EmptyState(title: state.filter == .ai ? "No AI-created instances" : "No user-created instances")
                }
            } else {
                if !pinnedItems.isEmpty {
                    DSSection("Pinned", subtitle: "Saved on this device", style: .plain) {
                        ForEach(pinnedItems) { inst in
                            pinCard(inst, busy: state.busyIds.contains(inst.id))
                        }
                    }
                }
                if !others.isEmpty {
                    DSSection(pinnedItems.isEmpty ? nil : "Other instances", rowMinHeight: 64) {
                        ForEach(others) { inst in
                            row(inst, busy: state.busyIds.contains(inst.id))
                        }
                    }
                }
            }
            if let message = state.errorMessage {
                Section { ErrorRow(message: message).dsClearRow() }
            }
        }
        .refreshable {
            await model.refreshNow()
            if let list = model.value?.instances {
                services.instanceRows.refreshDetached(list, brokerages: services.dashboard.brokeragesValue, force: true)
            }
        }
    }

    // MARK: Pinned card

    private func pinCard(_ inst: Instance, busy: Bool) -> some View {
        let rows = services.instanceRows
        let brokerages = services.dashboard.brokeragesValue
        let shown = rows.merged(inst)
        let accountId = inst.brokerageId.flatMap { $0.isEmpty ? nil : $0 }
        let paper = instanceIsPaper(shown, brokerages: brokerages)
        return InstancePinCard(
            inst: shown,
            detail: rows.details[inst.id],
            moneyKind: paper.map { $0 ? "Paper" : "Live" },
            kind: instanceKindLabel(shown, detailLoaded: rows.details[inst.id] != nil),
            accountName: accountId.map { instanceBrokerageName($0, nested: shown.brokerage, brokerages: brokerages) },
            summary: accountId.flatMap { rows.accounts.summary($0) },
            summaryPending: accountId.map { rows.accounts.isPending($0) } ?? false,
            strategyName: instanceStrategyName(shown),
            busy: busy,
            onOpen: { services.router.push(.instance(inst.id)) },
            onUnpin: { pinned.toggle(inst.id) },
            onStart: { Task { await model.start(inst.id) } },
            onStop: { Task { await model.stop(inst.id) } }
        ) {
            menuItems(inst, busy: busy, isPinned: true)
        }
        .dsClearRow(bottom: 16)
        .swipeActions(edge: .leading) { pinSwipe(inst, isPinned: true) }
        .swipeActions(edge: .trailing, allowsFullSwipe: false) { deleteSwipe(inst, busy: busy) }
        .contextMenu { menuItems(inst, busy: busy, isPinned: true) }
    }

    // MARK: Row

    private func row(_ inst: Instance, busy: Bool) -> some View {
        let isPinned = pinned.isPinned(inst.id)
        let rows = services.instanceRows
        let brokerages = services.dashboard.brokeragesValue
        let shown = rows.merged(inst)
        let accountId = inst.brokerageId.flatMap { $0.isEmpty ? nil : $0 }
        return NavigationLink(value: Route.instance(inst.id)) {
            InstanceRow(
                inst: shown,
                subtitle: instanceListSubtitle(shown, brokerages: brokerages),
                meta: instanceRowMeta(
                    uptime: rows.uptime(inst),
                    kind: instanceKindLabel(shown, detailLoaded: rows.details[inst.id] != nil)
                ),
                busy: busy,
                summary: accountId.flatMap { rows.accounts.summary($0) },
                summaryPending: accountId.map { rows.accounts.isPending($0) } ?? false
            )
        }
        .swipeActions(edge: .leading) { pinSwipe(inst, isPinned: isPinned) }
        .swipeActions(edge: .trailing, allowsFullSwipe: false) { deleteSwipe(inst, busy: busy) }
        .contextMenu { menuItems(inst, busy: busy, isPinned: isPinned) }
    }

    // MARK: Actions

    private func pinSwipe(_ inst: Instance, isPinned: Bool) -> some View {
        Button(isPinned ? "Unpin" : "Pin", systemImage: isPinned ? "pin.slash" : "pin") {
            pinned.toggle(inst.id)
        }
        .tint(DS.Swipe.neutral)
    }

    private func deleteSwipe(_ inst: Instance, busy: Bool) -> some View {
        Button("Delete", systemImage: "trash", role: .destructive) { confirmDelete(inst) }
            .disabled(busy || confirmRunning)
    }

    /// The context menu and a card's More menu (the web's ⋯ menu, with the
    /// app's actions).
    @ViewBuilder
    private func menuItems(_ inst: Instance, busy: Bool, isPinned: Bool) -> some View {
        Section {
            if inst.runCommand {
                Button("Stop", systemImage: "stop.fill") { Task { await model.stop(inst.id) } }
                    .disabled(busy)
            } else {
                Button("Start", systemImage: "play.fill") { Task { await model.start(inst.id) } }
                    .disabled(busy)
            }
            Button("Live trading", systemImage: "chart.xyaxis.line") {
                services.router.push(.liveTrading(inst.id))
            }
        }
        Section {
            Button(isPinned ? "Unpin" : "Pin", systemImage: isPinned ? "pin.slash" : "pin") {
                pinned.toggle(inst.id)
            }
            Button("Add stock", systemImage: "plus") {
                addStockFor = InstanceSheetTarget(id: inst.id)
            }
            .disabled(busy)
            Button("Copy ID", systemImage: "doc.on.doc") {
                UIPasteboard.general.string = inst.id
            }
        }
        Section {
            Button("Delete", systemImage: "trash", role: .destructive) { confirmDelete(inst) }
                .disabled(busy || confirmRunning)
        }
    }

    private func confirmDelete(_ inst: Instance) {
        confirm = instanceDeleteRequest(
            inst,
            // A failure shows on the list (errorMessage), as before.
            onConfirm: { _ = await model.delete(inst.id) },
            onError: { toast = Toast(swingErrorText($0), style: .error) }
        )
    }
}

/// The Delete instance confirmation, shared by the list's swipe and context
/// menu and Instance detail's toolbar menu. `onConfirm` runs the delete
/// (`InstancesModel.delete`).
func instanceDeleteRequest(
    _ inst: Instance,
    onConfirm: @escaping () async throws -> Void,
    onError: @escaping (any Error) -> Void
) -> ConfirmRequest {
    ConfirmRequest(
        title: "Delete instance",
        body: "Delete \"\(inst.name.isEmpty ? inst.id : inst.name)\"? This cannot be undone.",
        confirmLabel: "Delete",
        role: .destructive,
        onConfirm: onConfirm,
        onError: onError
    )
}

/// An instance row's subtitle: who made it (AI only; a user instance is the
/// default), the strategy, and the brokerage by name — "AI · Strategy 197 ·
/// Alpaca Paper". The brokerage name comes from the nested `brokerage` map,
/// else the already-loaded brokerage list; an id the app cannot name is
/// shortened in the middle.
func instanceRowSubtitle(_ inst: Instance, brokerages: [BrokerageAccount]?) -> String {
    var parts: [String] = []
    if inst.createdBy == "ai" { parts.append("AI") }
    if let sid = inst.strategyId {
        let name = (inst.strategy?["name"]).flatMap { $0.isNull ? nil : $0.dartDescription }
        parts.append(name ?? "Strategy \(sid)")
    } else {
        parts.append("No strategy linked")
    }
    if let bid = inst.brokerageId {
        parts.append(instanceBrokerageName(bid, nested: inst.brokerage, brokerages: brokerages))
    }
    return parts.joined(separator: " · ")
}

/// The list row's subtitle: `instanceRowSubtitle` with the money kind after
/// the account, as the web's pinned cards say it — "Strategy EB · Alpaca
/// Live · Live", "AI · Strategy F14 · Paper Test · Paper".
func instanceListSubtitle(_ inst: Instance, brokerages: [BrokerageAccount]?) -> String {
    let base = instanceRowSubtitle(inst, brokerages: brokerages)
    guard let paper = instanceIsPaper(inst, brokerages: brokerages) else { return base }
    return "\(base) · \(paper ? "Paper" : "Live")"
}

/// The linked strategy's name (the web's `pinStrategyName`): its own name,
/// else "Strategy <id>"; nil without a strategy.
func instanceStrategyName(_ inst: Instance) -> String? {
    guard let sid = inst.strategyId else { return nil }
    return (inst.strategy?["name"]).flatMap { $0.isNull ? nil : $0.dartDescription } ?? "Strategy \(sid)"
}

/// A brokerage id as a human name: the nested map's `account_name`, the
/// loaded list's name, or the id shortened in the middle (`bf78ad0c…3404`).
func instanceBrokerageName(_ id: String, nested: JSONObject?, brokerages: [BrokerageAccount]?) -> String {
    if let name = (nested?["account_name"] ?? .null).string, !name.isEmpty { return name }
    if let match = brokerages?.first(where: { $0.id == id }), !match.accountName.isEmpty { return match.accountName }
    return instanceShortId(id)
}

/// A long id shortened in the middle: the first 8 and last 4 characters.
func instanceShortId(_ id: String) -> String {
    guard id.count > 14 else { return id }
    return "\(id.prefix(8))…\(id.suffix(4))"
}

/// An id to present a sheet for (`.sheet(item:)`).
struct InstanceSheetTarget: Identifiable, Hashable {
    let id: String
}

/// Run state as a dot and a word: `Crashed` (red), `Running` (green,
/// pulsing) or `Stopped`, with an optional detail in the same line:
/// "● Running · Up 6h 12m".
struct InstanceStatusDot: View {
    let inst: Instance
    var font: Font = DS.Font.body
    var detail: String = ""

    var body: some View {
        let state = InstanceRunState(inst)
        StatusDot(detail.isEmpty ? state.word : "\(state.word) · \(detail)", tone: state.tone,
                  pulsing: state == .running, font: font)
    }
}

/// One instance in "Other instances", in the web's order: the status dot
/// and the name, then the strategy, account and Paper / Live, then the run
/// state with the uptime and kind; the account's equity and today's change
/// on the right when a brokerage is linked (the 1D line is the pinned
/// cards'; the web's rows draw none). At accessibility sizes the figures
/// move under the text.
///
///     ● Strategy EB                                $5,396.11
///       Strategy EB · Alpaca Live · Live           −$23.30 · −0.43%
///       Running · 6h 12m · Equity
private struct InstanceRow: View {
    let inst: Instance
    let subtitle: String
    let meta: String
    let busy: Bool
    let summary: DashboardAccountSummary?
    let summaryPending: Bool

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        let stacked = dynamicTypeSize.isAccessibilitySize
        let state = InstanceRunState(inst)
        let layout = stacked
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 6))
            : AnyLayout(HStackLayout(alignment: .center, spacing: 12))
        layout {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                DSStatusDot(state.tone)
                    .alignmentGuide(.firstTextBaseline) { $0[VerticalAlignment.center] + 4 }
                VStack(alignment: .leading, spacing: 2) {
                    Text(inst.name.isEmpty ? inst.id : inst.name)
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.primary)
                        .lineLimit(stacked ? nil : 1)
                    Text(subtitle)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .lineLimit(stacked ? nil : 1)
                    if busy {
                        HStack(spacing: 6) {
                            ProgressView()
                                .controlSize(.mini)
                            Text("Working…")
                        }
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                    } else {
                        Text(meta.isEmpty ? state.word : "\(state.word) · \(meta)")
                            .font(DS.Font.caption)
                            .foregroundStyle(state == .stopped ? DS.Text.faint : DS.Text.secondary)
                            .monospacedDigit()
                            .lineLimit(1)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            figures
                .layoutPriority(1)
        }
        .accessibilityElement(children: .combine)
    }

    /// The account's figures, skeleton lines while its first fetch runs, or
    /// nothing (no brokerage, or no history for its type).
    @ViewBuilder
    private var figures: some View {
        if let summary {
            EntityRowValue(
                fmtMoney(summary.equity),
                detail: instanceRowChangeText(summary),
                detailColor: summary.dayChange == nil ? nil : summary.direction.color
            )
        } else if summaryPending {
            VStack(alignment: .trailing, spacing: 6) {
                Skeleton(width: 72, height: 14)
                Skeleton(width: 96, height: 10)
            }
            .accessibilityHidden(true)
        }
    }
}

/// The list's loading shape (the web's): the subtitle, the filter, and rows
/// of static skeleton lines.
private struct InstancesSkeleton: View {
    var body: some View {
        List {
            Section {
                Text(instancesPageSub)
                    .font(DS.Font.pageSub)
                    .foregroundStyle(DS.Text.muted)
                    .dsClearRow(top: 0)
                Skeleton(width: 280, height: 34, radius: DS.Radius.control)
                    .dsClearRow(top: 16)
            }
            DSSection(rowMinHeight: 64) {
                ForEach(0..<6, id: \.self) { _ in
                    VStack(alignment: .leading, spacing: 8) {
                        Skeleton(width: 176, height: 16)
                        Skeleton(width: 112, height: 12)
                    }
                }
            }
        }
        .disabled(true)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Loading instances")
    }
}
