import SwiftUI

/// The Instances tab root — `InstancesScreen` in `instances_screen.dart`:
/// the equity instances (pinned first), a User/AI filter, and each card's
/// pin, view, live, start/stop and delete actions.
struct InstancesView: View {
    @Environment(AppServices.self) private var services

    var body: some View {
        InstancesContent(services: services)
    }
}

private struct InstancesContent: View {
    let services: AppServices

    @State private var model: InstancesModel
    @State private var pinned = PinnedInstancesModel()
    @State private var showCreate = false
    @State private var addStockFor: InstanceSheetTarget?
    @State private var confirm: ConfirmRequest?
    /// A confirmed delete is running: every Delete stays disabled.
    @State private var confirmRunning = false
    @State private var toast: Toast?

    init(services: AppServices) {
        self.services = services
        _model = State(initialValue: InstancesModel(repository: { [unowned services] in services.instanceRepository }))
    }

    var body: some View {
        content
            .background(DS.Surface.canvas)
            .navigationTitle("Instances")
            .navigationBarTitleDisplayMode(.large)
            .toolbar {
                ToolbarItemGroup(placement: .topBarTrailing) {
                    Button {
                        Task { await model.refreshNow() }
                    } label: {
                        Image(systemName: Symbol.named("refresh"))
                    }
                    .accessibilityLabel("Refresh")
                    Button {
                        showCreate = true
                    } label: {
                        Label("New Instance", systemImage: Symbol.named("add"))
                    }
                    .accessibilityLabel("New Instance")
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
            ScrollView {
                ErrorRow(message: model.state.errorMessage ?? "") {
                    Task { await model.reload() }
                }
                .padding(24)
            }
        case .loaded(let state):
            loadedBody(state)
        }
    }

    private func loadedBody(_ state: InstancesState) -> some View {
        let items = sortPinnedFirst(state.filtered, pinned.pinned)
        return ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("TRADING")
                        .font(.footnote.weight(.bold))
                        .tracking(1.2)
                        .foregroundStyle(.tint)
                    Text("Manage live trading and backtesting instances.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                .padding(.horizontal, 4)

                Picker("Filter", selection: Binding(get: { state.filter }, set: { model.setFilter($0) })) {
                    Text("All (\(state.allCount))").tag(InstanceFilter.all)
                    Text("User Created (\(state.userCount))").tag(InstanceFilter.user)
                    Text("AI Created (\(state.aiCount))").tag(InstanceFilter.ai)
                }
                .pickerStyle(.segmented)
                .padding(.bottom, 4)

                if state.instances.isEmpty {
                    EmptyState(
                        systemImage: Symbol.named("memory"),
                        title: "No instances yet",
                        subtitle: "Create an instance to run live trading or backtesting strategies.",
                        actionLabel: "Create Your First Instance",
                        onAction: { showCreate = true }
                    )
                } else if state.filtered.isEmpty {
                    EmptyState(
                        systemImage: Symbol.named("filter_alt_off"),
                        title: state.filter == .ai ? "No AI-created instances" : "No user-created instances"
                    )
                } else {
                    ForEach(items) { inst in
                        InstanceListCard(
                            inst: inst,
                            busy: state.busyIds.contains(inst.id),
                            pinned: pinned.isPinned(inst.id),
                            onPin: { pinned.toggle(inst.id) },
                            onView: { services.router.push(.instance(inst.id)) },
                            onLive: { services.router.push(.liveTrading(inst.id)) },
                            onStart: { Task { await model.start(inst.id) } },
                            onStop: { Task { await model.stop(inst.id) } },
                            deleteLocked: confirmRunning,
                            onDelete: { confirmDelete(inst) },
                            onAddStock: { addStockFor = InstanceSheetTarget(id: inst.id) },
                            onRemoveStock: { sym in
                                Task {
                                    do { try await model.removeStock(inst.id, sym) } catch {
                                        if !error.isCancellationOrTaskCancelled { toast = Toast(swingErrorText(error), style: .error) }
                                    }
                                }
                            }
                        )
                    }
                }
                if let message = state.errorMessage {
                    ErrorRow(message: message)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .padding(.bottom, 32)
        }
        .refreshable { await model.refreshNow() }
    }

    private func confirmDelete(_ inst: Instance) {
        confirm = ConfirmRequest(
            title: "Delete Instance",
            body: "Delete \"\(inst.name.isEmpty ? inst.id : inst.name)\"? This cannot be undone.",
            confirmLabel: "Delete",
            role: .destructive,
            onConfirm: { await model.delete(inst.id) },
            onError: { toast = Toast(swingErrorText($0), style: .error) }
        )
    }
}

/// An id to present a sheet for (`.sheet(item:)`).
struct InstanceSheetTarget: Identifiable, Hashable {
    let id: String
}

/// One instance card (`_InstanceCard`).
private struct InstanceListCard: View {
    let inst: Instance
    let busy: Bool
    let pinned: Bool
    let onPin: () -> Void
    let onView: () -> Void
    let onLive: () -> Void
    let onStart: () -> Void
    let onStop: () -> Void
    let deleteLocked: Bool
    let onDelete: () -> Void
    let onAddStock: () -> Void
    let onRemoveStock: (String) -> Void

    var body: some View {
        let isAi = inst.createdBy == "ai"
        let strategyName = (inst.strategy?["name"]).flatMap { $0.isNull ? nil : $0.dartDescription } ?? inst.strategyId ?? ""
        let brokerageName: String = inst.brokerage.map(instanceBrokerageLabel) ?? (inst.brokerageId ?? "")
        Card(padding: 16) {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .top, spacing: 12) {
                    IconTile(systemImage: Symbol.named("memory"))
                    VStack(alignment: .leading, spacing: 2) {
                        Text(inst.name.isEmpty ? inst.id : inst.name)
                            .font(.subheadline.weight(.semibold))
                            .lineLimit(1)
                        Text(inst.id)
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    VStack(alignment: .trailing, spacing: 6) {
                        AppBadge(label: isAi ? "AI" : "User", color: isAi ? DS.Palette.accent : .secondary)
                        InstanceStatusBadge(inst: inst)
                    }
                }

                VStack(alignment: .leading, spacing: 4) {
                    if inst.strategyId != nil {
                        InstanceMetaRow(label: "Strategy", value: strategyName)
                    } else {
                        Text("No strategy linked")
                            .font(.footnote)
                            .italic()
                            .foregroundStyle(.secondary)
                    }
                    if inst.brokerageId != nil {
                        InstanceMetaRow(label: "Brokerage", value: brokerageName)
                    }
                }

                InstanceStocksBlock(
                    title: "STOCKS (\(inst.stocks.count))",
                    stocks: inst.stocks,
                    busy: busy,
                    onAdd: onAddStock,
                    onRemove: onRemoveStock
                )

                DashboardFlowLayout(spacing: 6) {
                    InstanceActionButton(
                        label: pinned ? "Pinned" : "Pin",
                        symbol: Symbol.named(pinned ? "push_pin" : "push_pin_outlined"),
                        tint: pinned ? DS.Palette.warning : .secondary,
                        action: onPin
                    )
                    InstanceActionButton(label: "View", symbol: Symbol.named("open_in_new"), tint: DS.Palette.accent, action: onView)
                    InstanceActionButton(label: "Live", symbol: Symbol.named("show_chart"), tint: DS.Palette.info, action: onLive)
                    if inst.runCommand {
                        InstanceActionButton(label: "Stop", symbol: Symbol.named("stop"), tint: DS.Palette.warning, busy: busy, action: onStop)
                    } else {
                        InstanceActionButton(label: "Start", symbol: Symbol.named("play_arrow"), tint: DS.Palette.success, busy: busy, action: onStart)
                    }
                    InstanceActionButton(label: "Delete", symbol: Symbol.named("delete"), tint: DS.Palette.danger, disabled: busy || deleteLocked, action: onDelete)
                }
            }
        }
    }
}

/// `Crashed` (red) / `Running` (green, pulsing) / `Stopped`.
struct InstanceStatusBadge: View {
    let inst: Instance

    var body: some View {
        let label = inst.crashed ? "Crashed" : (inst.runCommand ? "Running" : "Stopped")
        let color: Color = inst.crashed ? DS.Palette.danger : (inst.runCommand ? DS.Palette.success : .secondary)
        StatusBadge(label: label, color: color, pulsing: inst.runCommand && !inst.crashed)
    }
}

/// `Label: value` (`_MetaRow`).
private struct InstanceMetaRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack(spacing: 0) {
            Text("\(label): ")
                .foregroundStyle(.secondary)
            Text(value)
                .lineLimit(1)
        }
        .font(.footnote)
    }
}

/// A small tinted action (`_ActionBtn`): disabled at 40 % while busy, with a
/// spinner in place of the glyph when it is the busy action.
struct InstanceActionButton: View {
    let label: String
    let symbol: String
    let tint: Color
    var busy = false
    var disabled = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                if busy {
                    ProgressView().controlSize(.mini)
                } else {
                    Image(systemName: symbol)
                }
                Text(label)
            }
            .font(.caption.weight(.semibold))
        }
        .buttonStyle(.bordered)
        .controlSize(.small)
        .tint(tint)
        .disabled(busy || disabled)
        .frame(minHeight: 44)
    }
}

/// The stocks block shared by the list card and the detail card: a header
/// with Add, removable chips, or `No stocks added`.
struct InstanceStocksBlock: View {
    let title: String
    let stocks: [String]
    var busy = false
    var titleFont: Font = .caption2.weight(.semibold)
    let onAdd: () -> Void
    let onRemove: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(title)
                    .font(titleFont)
                    .tracking(0.8)
                    .foregroundStyle(.secondary)
                Spacer()
                Button(action: onAdd) {
                    Label("Add", systemImage: Symbol.named("add"))
                        .font(.caption.weight(.semibold))
                }
                .buttonStyle(.borderless)
                .disabled(busy)
                .frame(minHeight: 44)
            }
            if stocks.isEmpty {
                Text("No stocks added")
                    .font(.footnote)
                    .italic()
                    .foregroundStyle(.secondary)
            } else {
                DashboardFlowLayout(spacing: 6) {
                    ForEach(stocks, id: \.self) { sym in
                        HStack(spacing: 4) {
                            Text(sym)
                                .font(.caption.monospaced())
                            if !busy {
                                Button {
                                    onRemove(sym)
                                } label: {
                                    Image(systemName: Symbol.named("close"))
                                        .font(.caption2.weight(.semibold))
                                        .foregroundStyle(.secondary)
                                        .frame(width: 22, height: 22)
                                        .contentShape(Rectangle())
                                }
                                .buttonStyle(.plain)
                                .accessibilityLabel("Remove \(sym)")
                            }
                        }
                        .padding(.leading, 8)
                        .padding(.trailing, busy ? 8 : 2)
                        .padding(.vertical, 3)
                        .background(DS.Surface.inset, in: .rect(cornerRadius: 6, style: .continuous))
                    }
                }
            }
        }
    }
}

/// The list skeleton: pills and four card shapes.
private struct InstancesSkeleton: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Skeleton(height: 12, radius: 6)
                Skeleton(height: 30, radius: 8)
                ForEach(0..<4, id: \.self) { _ in
                    Card(padding: 16) {
                        VStack(alignment: .leading, spacing: 12) {
                            HStack(spacing: 12) {
                                Skeleton.circle(40)
                                VStack(alignment: .leading, spacing: 6) {
                                    Skeleton(width: 140, height: 14, radius: 6)
                                    Skeleton(width: 100, height: 10, radius: 5)
                                }
                                Spacer()
                                Skeleton(width: 56, height: 18, radius: 9)
                            }
                            Skeleton(height: 12, radius: 6)
                            Skeleton(width: 220, height: 12, radius: 6)
                            Skeleton(height: 28, radius: 8)
                        }
                    }
                }
            }
            .padding(16)
        }
        .scrollDisabled(true)
        .accessibilityLabel("Loading")
    }
}
