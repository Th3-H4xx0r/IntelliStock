import SwiftUI

/// The Kalshi tab — `KalshiScreen` in kalshi_screen.dart: account selector,
/// the brokerage's instances, portfolio hero, Edge Radar, open positions and
/// live logs. Start / Stop / KILL live on the instance screen. A plain
/// large-title screen: the Dart gradient crown is gone.
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

    @ViewBuilder
    private func content(_ model: KalshiOverviewModel, accounts: [BrokerageAccount]) -> some View {
        if let selectedId = model.selectedId {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if accounts.count > 1 {
                        accountSelector(model, accounts: accounts, selectedId: selectedId)
                    }
                    let instances = model.instanceList(selectedId)
                    if model.instances[selectedId]?.value == nil && model.isLoadingInstances(selectedId) {
                        LoadingState().padding(.top, 40)
                    } else if let first = instances.first {
                        instanceList(instances)
                        Button {
                            createSheet = CreateSheet(accounts: accounts, brokerageId: selectedId)
                        } label: {
                            Label("New Instance", systemImage: Symbol.named("add"))
                                .fontWeight(.semibold)
                        }
                        .buttonStyle(.borderless)
                        .padding(.leading, 4)
                        KalshiPortfolioHero(
                            title: "Portfolio value",
                            state: model.portfolio[selectedId],
                            onRetry: { Task { await model.loadPortfolio(selectedId) } }
                        )
                        edgeRadar(model, bid: selectedId)
                        positionsCard(model, bid: selectedId)
                        Card(padding: 16) {
                            VStack(alignment: .leading, spacing: 12) {
                                MarketsCardHeader(icon: "terminal", title: "Live logs")
                                LiveLogsPanel(instanceId: first.id)
                                    .id(first.id)
                                    .frame(height: 300)
                            }
                        }
                    } else {
                        EmptyState(
                            systemImage: Symbol.named("smart_toy"),
                            title: "No trading instance yet",
                            subtitle: "Create a Kalshi instance to scan soccer markets, flag edge, and (when started) trade.",
                            actionLabel: "Create Instance",
                            onAction: { createSheet = CreateSheet(accounts: accounts, brokerageId: selectedId) }
                        )
                    }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 24)
            }
            .refreshable { await model.refresh() }
        } else {
            EmptyState(
                systemImage: Symbol.named("sports_soccer"),
                title: "No Kalshi account linked",
                subtitle: "Link a Kalshi brokerage (demo or live) to create a trading instance."
            )
        }
    }

    private func accountSelector(_ model: KalshiOverviewModel, accounts: [BrokerageAccount], selectedId: String) -> some View {
        Picker("Account", selection: Binding(
            get: { selectedId },
            set: { id in
                model.select(id)
                Task { await model.loadIfNeeded(id) }
            }
        )) {
            ForEach(accounts) { a in Text(a.accountName).tag(a.id) }
        }
        .pickerStyle(.menu)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
    }

    /// Every instance for this brokerage — tap one to manage it. Only one may
    /// run per brokerage (the backend enforces it on Start).
    private func instanceList(_ instances: [KalshiInstance]) -> some View {
        VStack(spacing: 0) {
            ForEach(Array(instances.enumerated()), id: \.element.id) { i, inst in
                if i > 0 { Divider().padding(.leading, 34) }
                NavigationLink(value: Route.kalshiInstance(inst.id)) {
                    HStack(spacing: 10) {
                        Circle()
                            .fill(inst.running ? DS.Palette.success : Color(uiColor: .quaternaryLabel))
                            .frame(width: 8, height: 8)
                        Text(inst.name)
                            .font(.body.weight(.semibold))
                            .foregroundStyle(.primary)
                            .lineLimit(1)
                        Spacer(minLength: 8)
                        MarketsTag(text: inst.running ? "Running" : "Stopped", color: inst.running ? DS.Palette.success : .secondary)
                        MarketsTag(text: inst.liveEnabled ? "Live" : "Paper", color: inst.liveEnabled ? DS.Palette.danger : DS.Palette.accent)
                        Image(systemName: Symbol.named("chevron_right"))
                            .font(.footnote.weight(.semibold))
                            .foregroundStyle(.tertiary)
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 12)
                    .contentShape(.rect)
                }
                .buttonStyle(.plain)
            }
        }
        .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.card, style: .continuous))
    }

    private func edgeRadar(_ model: KalshiOverviewModel, bid: String) -> some View {
        Card(padding: 16) {
            VStack(alignment: .leading, spacing: 12) {
                MarketsCardHeader(icon: "bolt", title: "Edge Radar")
                switch model.edges[bid] {
                case .failed(let e):
                    ErrorRow(message: KalshiFormat.errorText(e), onRetry: { Task { await model.loadEdges(bid) } })
                case .loaded(let edges):
                    if edges.isEmpty {
                        Text("No +EV contracts right now.")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    } else {
                        VStack(spacing: 0) {
                            ForEach(Array(edges.enumerated()), id: \.offset) { _, e in
                                HStack {
                                    Text("\(e.marketTicker)  ·  \(e.side)")
                                        .font(.subheadline)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(1)
                                    Spacer()
                                    Text("+\(dartToStringAsFixed(e.edge * 100, 1))%")
                                        .font(.subheadline.weight(.semibold).monospacedDigit())
                                        .foregroundStyle(DS.Palette.success)
                                }
                                .padding(.vertical, 5)
                            }
                        }
                    }
                case .loading, .none:
                    LoadingState()
                }
            }
        }
    }

    private func positionsCard(_ model: KalshiOverviewModel, bid: String) -> some View {
        Card(padding: 16) {
            VStack(alignment: .leading, spacing: 12) {
                MarketsCardHeader(icon: "receipt_long", title: "Open positions")
                switch model.positions[bid] {
                case .failed(let e):
                    ErrorRow(message: KalshiFormat.errorText(e), onRetry: { Task { await model.loadPositions(bid) } })
                case .loaded(let positions):
                    if positions.isEmpty {
                        Text("No open positions.")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    } else {
                        VStack(spacing: 10) {
                            ForEach(Array(positions.enumerated()), id: \.offset) { _, p in
                                KalshiPositionTile(position: p)
                            }
                        }
                    }
                case .loading, .none:
                    LoadingState()
                }
            }
        }
    }
}
