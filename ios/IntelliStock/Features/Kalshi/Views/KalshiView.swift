import SwiftUI

/// The Kalshi tab — `KalshiScreen` in kalshi_screen.dart: account selector,
/// the brokerage's instances, portfolio hero, Edge Radar, open positions and
/// live logs. Start / Stop / KILL live on the instance screen. An
/// inset-grouped list under a large title: the account switcher and the `+`
/// sit in the toolbar.
struct KalshiView: View {
    @Environment(AppServices.self) private var services
    @State private var model: KalshiOverviewModel?
    @State private var createSheet: CreateSheet?

    private struct CreateSheet: Identifiable {
        let id = UUID()
        let accounts: [BrokerageAccount]
        let brokerageId: String
    }

    var body: some View {
        let accounts = KalshiOverviewModel.kalshiAccounts(services.dashboard.brokeragesValue)
        Group {
            if let model {
                content(model, accounts: accounts)
            } else {
                Color.clear
            }
        }
        .background(DS.Surface.canvas)
        .navigationTitle("Kalshi")
        .navigationBarTitleDisplayMode(.large)
        .toolbar { toolbar(accounts: accounts) }
        .task {
            if model == nil {
                model = KalshiOverviewModel(repository: { [services] in services.kalshiRepository })
            }
            await services.dashboard.loadBrokerages()
        }
        .onChange(of: accounts.map(\.id), initial: true) { _, _ in
            guard let model else { return }
            if let bid = model.reconcile(accounts: accounts) {
                Task { await model.loadIfNeeded(bid) }
            }
        }
        .onChange(of: model == nil) { _, _ in
            guard let model else { return }
            if let bid = model.reconcile(accounts: accounts) {
                Task { await model.loadIfNeeded(bid) }
            }
        }
        .sheet(item: $createSheet) { sheet in
            KalshiInstanceSheet(
                accounts: sheet.accounts,
                initialBrokerageId: sheet.brokerageId,
                repository: { [services] in services.kalshiRepository },
                onCreated: { bid in Task { await model?.loadInstances(bid) } }
            )
        }
    }

    // MARK: Toolbar

    /// The account switcher (a toolbar `Menu`, only with more than one Kalshi
    /// account, as the Dart selector) and the `+` for a new instance.
    @ToolbarContentBuilder
    private func toolbar(accounts: [BrokerageAccount]) -> some ToolbarContent {
        if let model, let selectedId = model.selectedId {
            if accounts.count > 1 {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Picker("Account", selection: Binding(
                            get: { selectedId },
                            set: { id in
                                model.select(id)
                                Task { await model.loadIfNeeded(id) }
                            }
                        )) {
                            ForEach(accounts) { a in Text(a.accountName).tag(a.id) }
                        }
                    } label: {
                        HStack(spacing: 4) {
                            Text(accounts.first { $0.id == selectedId }?.accountName ?? "Account")
                                .lineLimit(1)
                            Image(systemName: "chevron.up.chevron.down")
                                .font(.caption.weight(.semibold))
                                .accessibilityHidden(true)
                        }
                        .accessibilityLabel("Account")
                        .accessibilityValue(accounts.first { $0.id == selectedId }?.accountName ?? "")
                    }
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                ToolbarAddButton("New Instance") {
                    createSheet = CreateSheet(accounts: accounts, brokerageId: selectedId)
                }
            }
        }
    }

    // MARK: Content

    @ViewBuilder
    private func content(_ model: KalshiOverviewModel, accounts: [BrokerageAccount]) -> some View {
        if let selectedId = model.selectedId {
            let instances = model.instanceList(selectedId)
            if model.instances[selectedId]?.value == nil && model.isLoadingInstances(selectedId) {
                ProgressView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let first = instances.first {
                List {
                    instanceSection(instances)
                    KalshiPortfolioHero(
                        title: "Portfolio value",
                        state: model.portfolio[selectedId],
                        onRetry: { Task { await model.loadPortfolio(selectedId) } }
                    )
                    edgeRadar(model, bid: selectedId)
                    positionsSection(model, bid: selectedId)
                    // The log tail opens in place from its one row.
                    Section("Live logs") {
                        LiveLogsPanel(instanceId: first.id)
                            .id(first.id)
                            .listRowInsets(EdgeInsets())
                    }
                }
                .listStyle(.insetGrouped)
                .refreshable { await model.refresh() }
            } else {
                EmptyState(
                    systemImage: Symbol.named("smart_toy"),
                    title: "No trading instance yet",
                    subtitle: "Create a Kalshi instance to scan soccer markets, flag edge, and (when started) trade.",
                    actionLabel: "Create Instance",
                    onAction: { createSheet = CreateSheet(accounts: accounts, brokerageId: selectedId) }
                )
            }
        } else {
            EmptyState(
                systemImage: Symbol.named("sports_soccer"),
                title: "No Kalshi account linked",
                subtitle: "Link a Kalshi brokerage (demo or live) to create a trading instance."
            )
        }
    }

    /// Every instance for this brokerage — tap one to manage it. Only one may
    /// run per brokerage (the backend enforces it on Start).
    private func instanceSection(_ instances: [KalshiInstance]) -> some View {
        Section("Instances") {
            ForEach(instances) { inst in
                NavigationLink(value: Route.kalshiInstance(inst.id)) {
                    EntityRow(inst.name, subtitle: inst.liveEnabled ? "Live" : "Paper") {
                        StatusDot(
                            inst.running ? "Running" : "Stopped",
                            color: inst.running ? DS.Palette.success : .secondary,
                            pulsing: inst.running
                        )
                    }
                }
            }
        }
    }

    private func edgeRadar(_ model: KalshiOverviewModel, bid: String) -> some View {
        Section("Edge radar") {
            switch model.edges[bid] {
            case .failed(let e):
                ErrorRow(message: KalshiFormat.errorText(e), onRetry: { Task { await model.loadEdges(bid) } })
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
            case .loaded(let edges):
                if edges.isEmpty {
                    Text("No +EV contracts right now.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(Array(edges.enumerated()), id: \.offset) { _, e in
                        LabeledContent {
                            Text("+\(dartToStringAsFixed(e.edge * 100, 1))%")
                                .monospacedDigit()
                                .foregroundStyle(DS.Palette.success)
                        } label: {
                            Text("\(e.marketTicker)  ·  \(e.side)")
                                .lineLimit(1)
                        }
                    }
                }
            case .loading, .none:
                LoadingState()
            }
        }
    }

    private func positionsSection(_ model: KalshiOverviewModel, bid: String) -> some View {
        Section("Open positions") {
            switch model.positions[bid] {
            case .failed(let e):
                ErrorRow(message: KalshiFormat.errorText(e), onRetry: { Task { await model.loadPositions(bid) } })
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
            case .loaded(let positions):
                if positions.isEmpty {
                    Text("No open positions.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(Array(positions.enumerated()), id: \.offset) { _, p in
                        KalshiPositionRow(position: p)
                    }
                }
            case .loading, .none:
                LoadingState()
            }
        }
    }
}
