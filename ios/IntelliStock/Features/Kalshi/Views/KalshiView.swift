import SwiftUI

/// The Kalshi tab — `KalshiScreen` in kalshi_screen.dart, as the web's
/// Prediction markets page: the brokerage's instances, the portfolio card,
/// Edge radar, open positions and live logs. Start / Stop / Kill live on the
/// instance screen. The account switcher and the `+` sit in the toolbar.
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
        .background(DS.Surface.page)
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
                                .font(.system(size: 12))
                                .accessibilityHidden(true)
                        }
                        .accessibilityLabel("Account")
                        .accessibilityValue(accounts.first { $0.id == selectedId }?.accountName ?? "")
                    }
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                ToolbarAddButton("New instance") {
                    createSheet = CreateSheet(accounts: accounts, brokerageId: selectedId)
                }
            }
        }
    }

    // MARK: Content

    static let subtitle = "Prediction markets · autonomous soccer event-contract trading"

    private func content(_ model: KalshiOverviewModel, accounts: [BrokerageAccount]) -> some View {
        List {
            Section {
                Text(Self.subtitle)
                    .font(DS.Font.pageSub)
                    .foregroundStyle(DS.Text.muted)
                    .dsClearRow(top: 0)
            }
            if let selectedId = model.selectedId {
                let instances = model.instanceList(selectedId)
                if model.instances[selectedId]?.value == nil && model.isLoadingInstances(selectedId) {
                    DSSection("Instances") {
                        ForEach(0..<2, id: \.self) { _ in
                            VStack(alignment: .leading, spacing: 8) {
                                Skeleton(width: 160, height: 14)
                                Skeleton(width: 60, height: 12)
                            }
                        }
                    }
                    .allowsHitTesting(false)
                    .accessibilityLabel("Loading instances")
                } else if let first = instances.first {
                    instanceSection(instances)
                    KalshiPortfolioHero(
                        title: "Portfolio value",
                        state: model.portfolio[selectedId],
                        onRetry: { Task { await model.loadPortfolio(selectedId) } }
                    )
                    edgeRadar(model, bid: selectedId)
                    positionsSection(model, bid: selectedId)
                    // The log tail opens in place from its header.
                    DSSection("Live logs", subtitle: first.name, style: .plain) {
                        LiveLogsPanel(instanceId: first.id)
                            .id(first.id)
                            .dsClearRow()
                    }
                } else {
                    let name = accounts.first { $0.id == selectedId }?.accountName
                    DSSection {
                        EmptyState(
                            title: "No trading instance yet",
                            subtitle: "Create a Kalshi trading instance\(name.map { " for \($0)" } ?? "") to start scanning soccer markets, flagging edge and, once started, trading",
                            actionLabel: "Create Kalshi instance",
                            onAction: { createSheet = CreateSheet(accounts: accounts, brokerageId: selectedId) }
                        )
                    }
                }
            } else {
                DSSection {
                    EmptyState(
                        title: "No Kalshi account linked",
                        subtitle: "Link a demo or live Kalshi brokerage to create a trading instance here"
                    )
                }
            }
        }
        .dsList()
        .refreshable { await model.refresh() }
    }

    /// Every instance for this brokerage — tap one to manage it. Only one may
    /// run per brokerage (the backend enforces it on Start).
    private func instanceSection(_ instances: [KalshiInstance]) -> some View {
        DSSection("Instances", subtitle: "One may run per brokerage") {
            ForEach(instances) { inst in
                NavigationLink(value: Route.kalshiInstance(inst.id)) {
                    EntityRow(inst.name, subtitle: inst.liveEnabled ? "Live · real money" : "Paper") {
                        StatusDot(
                            inst.running ? "Running" : "Stopped",
                            tone: inst.running ? .live : .off,
                            pulsing: inst.running
                        )
                    }
                }
            }
        }
    }

    private func edgeRadar(_ model: KalshiOverviewModel, bid: String) -> some View {
        DSSection("Edge radar", subtitle: "+EV contracts flagged on the last scan") {
            switch model.edges[bid] {
            case .failed(let e):
                HStack(spacing: 16) {
                    Text(KalshiFormat.errorText(e))
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Palette.loss)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Button("Retry") { Task { await model.loadEdges(bid) } }
                        .buttonStyle(.dsSecondary)
                }
            case .loaded(let edges):
                if edges.isEmpty {
                    Text("No +EV contracts flagged right now")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.muted)
                } else {
                    ForEach(Array(edges.enumerated()), id: \.offset) { _, e in
                        HStack(spacing: 8) {
                            // A long ticker gives up its middle, never the side.
                            Text(e.marketTicker)
                                .foregroundStyle(DS.Text.primary)
                                .lineLimit(1)
                                .truncationMode(.middle)
                            Text("· \(e.side)")
                                .foregroundStyle(DS.Text.muted)
                                .fixedSize()
                            Spacer(minLength: 8)
                            Text("+\(dartToStringAsFixed(e.edge * 100, 1))%")
                                .monospacedDigit()
                                .foregroundStyle(DS.Palette.gain)
                        }
                        .font(DS.Font.body)
                        .accessibilityElement(children: .combine)
                    }
                }
            case .loading, .none:
                Skeleton(width: 200, height: 14)
                    .accessibilityLabel("Loading")
            }
        }
    }

    private func positionsSection(_ model: KalshiOverviewModel, bid: String) -> some View {
        let count = model.positions[bid]?.value?.count
        return DSSection("Open positions", subtitle: count.map { "\($0) position\($0 == 1 ? "" : "s")" }, rowMinHeight: 64) {
            switch model.positions[bid] {
            case .failed(let e):
                HStack(spacing: 16) {
                    Text(KalshiFormat.errorText(e))
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Palette.loss)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Button("Retry") { Task { await model.loadPositions(bid) } }
                        .buttonStyle(.dsSecondary)
                }
            case .loaded(let positions):
                if positions.isEmpty {
                    Text("No open positions")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.muted)
                } else {
                    ForEach(Array(positions.enumerated()), id: \.offset) { _, p in
                        KalshiPositionRow(position: p)
                    }
                }
            case .loading, .none:
                Skeleton(width: 200, height: 14)
                    .accessibilityLabel("Loading")
            }
        }
    }
}
