import SwiftUI

/// The Instances tab root — `InstancesScreen` in `instances_screen.dart`:
/// the equity instances (pinned first) as an inset-grouped list under a
/// User/AI filter. A row opens the instance; its pin, live, start/stop, add
/// stock and delete actions sit in swipe actions and the context menu.
struct InstancesView: View {
    @Environment(AppServices.self) private var services

    var body: some View {
        InstancesContent(services: services)
    }
}

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
            .listStyle(.insetGrouped)
            .navigationTitle("Instances")
            .navigationBarTitleDisplayMode(.large)
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    ToolbarAddButton("New Instance") { showCreate = true }
                }
            }
            .task { await model.poll(lifecycle: services.lifecycle) }
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

    @ViewBuilder
    private var content: some View {
        switch model.state {
        case .loading:
            InstancesSkeleton()
        case .failed:
            List {
                Section {
                    ErrorRow(message: model.state.errorMessage ?? "") {
                        Task { await model.reload() }
                    }
                }
            }
        case .loaded(let state):
            loadedBody(state)
        }
    }

    private func loadedBody(_ state: InstancesState) -> some View {
        let items = sortPinnedFirst(state.filtered, pinned.pinned)
        return List {
            Section {
                Picker("Filter", selection: Binding(get: { state.filter }, set: { model.setFilter($0) })) {
                    Text("All (\(state.allCount))").tag(InstanceFilter.all)
                    Text("User Created (\(state.userCount))").tag(InstanceFilter.user)
                    Text("AI Created (\(state.aiCount))").tag(InstanceFilter.ai)
                }
                .pickerStyle(.segmented)
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets())
            }

            if state.instances.isEmpty {
                Section {
                    EmptyState(
                        systemImage: Symbol.named("memory"),
                        title: "No instances yet",
                        subtitle: "Create an instance to run live trading or backtesting strategies.",
                        actionLabel: "Create Your First Instance",
                        onAction: { showCreate = true }
                    )
                    .listRowBackground(Color.clear)
                }
            } else if state.filtered.isEmpty {
                Section {
                    EmptyState(
                        systemImage: Symbol.named("filter_alt_off"),
                        title: state.filter == .ai ? "No AI-created instances" : "No user-created instances"
                    )
                    .listRowBackground(Color.clear)
                }
            } else {
                Section {
                    ForEach(items) { inst in
                        row(inst, busy: state.busyIds.contains(inst.id))
                    }
                }
            }
            if let message = state.errorMessage {
                Section { ErrorRow(message: message) }
            }
        }
        .refreshable { await model.refreshNow() }
    }

    // MARK: Row

    private func row(_ inst: Instance, busy: Bool) -> some View {
        let isPinned = pinned.isPinned(inst.id)
        return NavigationLink(value: Route.instance(inst.id)) {
            EntityRow(
                inst.name.isEmpty ? inst.id : inst.name,
                subtitle: instanceRowSubtitle(inst, brokerages: services.dashboard.brokeragesValue),
                subtitleLineLimit: 2,
                isPinned: isPinned
            ) {
                if busy {
                    ProgressView()
                } else {
                    InstanceStatusDot(inst: inst)
                }
            }
        }
        .swipeActions(edge: .leading) {
            Button(isPinned ? "Unpin" : "Pin", systemImage: isPinned ? "pin.slash" : "pin") {
                pinned.toggle(inst.id)
            }
            .tint(.orange)
        }
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            Button("Delete", systemImage: "trash", role: .destructive) { confirmDelete(inst) }
                .disabled(busy || confirmRunning)
        }
        .contextMenu {
            Section {
                if inst.runCommand {
                    Button("Stop", systemImage: "stop.fill") { Task { await model.stop(inst.id) } }
                        .disabled(busy)
                } else {
                    Button("Start", systemImage: "play.fill") { Task { await model.start(inst.id) } }
                        .disabled(busy)
                }
                Button("View Live", systemImage: "chart.xyaxis.line") {
                    services.router.push(.liveTrading(inst.id))
                }
            }
            Section {
                Button(isPinned ? "Unpin" : "Pin", systemImage: isPinned ? "pin.slash" : "pin") {
                    pinned.toggle(inst.id)
                }
                Button("Add Stock…", systemImage: "plus") {
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

/// The Delete Instance confirmation, shared by the list's swipe and context
/// menu and Instance detail's toolbar menu. `onConfirm` runs the delete
/// (`InstancesModel.delete`).
func instanceDeleteRequest(
    _ inst: Instance,
    onConfirm: @escaping () async throws -> Void,
    onError: @escaping (any Error) -> Void
) -> ConfirmRequest {
    ConfirmRequest(
        title: "Delete Instance",
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
/// pulsing) or `Stopped`.
struct InstanceStatusDot: View {
    let inst: Instance

    var body: some View {
        let label = inst.crashed ? "Crashed" : (inst.runCommand ? "Running" : "Stopped")
        let color: Color = inst.crashed ? DS.Palette.danger : (inst.runCommand ? DS.Palette.success : .secondary)
        StatusDot(label, color: color, pulsing: inst.runCommand && !inst.crashed)
    }
}

/// The list's loading shape: the filter and rows, redacted.
private struct InstancesSkeleton: View {
    var body: some View {
        List {
            Section {
                Picker("Filter", selection: .constant(0)) {
                    Text("All").tag(0)
                    Text("User Created").tag(1)
                    Text("AI Created").tag(2)
                }
                .pickerStyle(.segmented)
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets())
            }
            Section {
                ForEach(0..<6, id: \.self) { _ in
                    EntityRow("Instance name", subtitle: "Strategy 000 · Brokerage") {
                        StatusDot("Stopped", color: .secondary)
                    }
                }
            }
        }
        .redacted(reason: .placeholder)
        .disabled(true)
        .accessibilityLabel("Loading")
    }
}
