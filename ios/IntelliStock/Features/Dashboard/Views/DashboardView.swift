import SwiftUI

/// The Dashboard tab root — `DashboardScreen` in `dashboard_screen.dart`.
///
/// Sections in Dart's order: Portfolio (hero, chart, holdings) · Insights ·
/// Market · Strategy · Kalshi · Services · Re-run onboarding, as one
/// inset-grouped `List` (redesign spec 2026-10-02). There is no visible
/// title: the balance is the first and biggest thing on screen, as in Stocks,
/// and only the search button sits in the bar.
///
/// A `List` builds its rows lazily, so every load and poll a section used to
/// start from its own `.task` runs from here, on the list itself: each card
/// still loads once on entry and keeps polling while the dashboard is on
/// screen, whatever is scrolled into view (Dart's `cacheExtent: 10000`).
struct DashboardView: View {
    @Environment(AppServices.self) private var services

    var body: some View {
        DashboardContent(services: services)
    }
}

/// The search action that sits top-right (`DashboardTopActions`).
enum DashboardTopActions {
    static let searchLabel = "Search symbols"
    static let searchRoute = Route.search
    /// The navigation title: the back button's label and what VoiceOver
    /// reads. It is never drawn; the hero carries no heading either.
    static let title = "Dashboard"
}

/// The dashboard's search button. The Dashboard root hides its navigation
/// bar, so this sits at the trailing end of the hero instead: the same
/// glass circle, glyph and label the bar's button had, and a 44 pt target.
struct DashboardSearchButton: View {
    @Environment(AppServices.self) private var services

    var body: some View {
        Button {
            services.router.push(DashboardTopActions.searchRoute)
        } label: {
            Image(systemName: Symbol.named("search"))
                .font(.title3.weight(.medium))
                .foregroundStyle(Color.primary)
                .frame(width: 44, height: 44)
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .glassEffect(.regular.interactive(), in: Circle())
        .accessibilityLabel(DashboardTopActions.searchLabel)
    }
}

/// The search button on its own row, for the states with no hero beside it
/// (no accounts, or the account list failed to load).
private struct DashboardSearchRow: View {
    var body: some View {
        Section {
            HStack {
                Spacer()
                DashboardSearchButton()
            }
            .listRowBackground(Color.clear)
            .listRowInsets(EdgeInsets(top: 0, leading: 0, bottom: 0, trailing: 0))
        }
    }
}

private struct DashboardContent: View {
    let services: AppServices

    @State private var feed: DashboardFeedModel
    @State private var strategy: NexusStrategyModel
    @State private var portfolios: DashboardPortfoliosModel
    @State private var kalshi = KalshiDashboardCardModel()
    @State private var scope: DashboardAccountScope?
    @State private var widgetSynced = false
    @State private var portfoliosPrefetched = false
    @State private var showPortfolios = false
    @State private var browserLink: DashboardBrowserLink?
    @State private var startAgentSheet = false

    init(services: AppServices) {
        self.services = services
        _feed = State(initialValue: DashboardFeedModel(
            loader: { [unowned services] in DashboardInsightsLoader(client: services.apiClient) }
        ))
        _strategy = State(initialValue: NexusStrategyModel(
            repository: { [unowned services] in services.dashboardRepository }
        ))
        _portfolios = State(initialValue: DashboardPortfoliosModel(
            fetcher: { [unowned services] in
                DashboardPortfolios.fetcher(dashboard: services.dashboardRepository, kalshi: services.kalshiRepository)
            }
        ))
    }

    private var accounts: [BrokerageAccount]? { services.dashboard.brokerages.value }

    /// The account the hero shows: the stored selection when present, else
    /// the first account.
    private var selected: BrokerageAccount? {
        accounts.flatMap { DashboardFormat.resolveSelected($0, services.selectedAccount.selectedId) }
    }

    /// The scope, while it belongs to the selected account.
    private var liveScope: DashboardAccountScope? {
        guard let scope, let selected, scope.brokerageId == selected.id else { return nil }
        return scope
    }

    /// The scope while the portfolio section shows (brokerages loaded and
    /// non-empty): the hero and holdings poll only then, as they did when
    /// they lived in that section.
    private var portfolioScope: DashboardAccountScope? {
        guard case .loaded(let list) = services.dashboard.brokerages, !list.isEmpty else { return nil }
        return liveScope
    }

    private var kalshiAccount: BrokerageAccount? {
        (services.dashboard.brokeragesValue ?? []).first { $0.brokerageType == "kalshi" }
    }

    var body: some View {
        List {
            portfolio
            if let scope = liveScope, let selected {
                DashboardInsightsSections(scope: scope, feed: feed) { browserLink = $0 }
                DashboardStrategySections(brokerageId: selected.id, model: strategy)
            }
            if let kalshiAccount {
                KalshiDashboardCard(account: kalshiAccount, model: kalshi)
            }
            DashboardServicesSection { startAgentSheet = true }
            onboardingSection
        }
        .listStyle(.insetGrouped)
        // The hero starts right at the top of the safe area, as in Stocks.
        .contentMargins(.top, 0, for: .scrollContent)
        .navigationTitle(DashboardTopActions.title)
        .navigationBarTitleDisplayMode(.inline)
        // No bar: the balance leads the screen and the search button sits at
        // the hero's trailing end (`DashboardSearchButton`).
        .toolbar(.hidden, for: .navigationBar)
        // Rows scrolled up pass under a plain strip, never under the clock.
        .overlay(alignment: .top) {
            Color(uiColor: .systemGroupedBackground)
                .frame(height: 0)
                .ignoresSafeArea(edges: .top)
                .allowsHitTesting(false)
                .accessibilityHidden(true)
        }
        .refreshable { await refreshAll() }
        .task {
            // Refresh the home-screen widget (live instance equity) on entry.
            if !widgetSynced {
                widgetSynced = true
                Task { await services.widgetDataSyncer.run() }
            }
            if services.dashboard.brokerages.value == nil {
                await services.dashboard.loadBrokerages()
            }
        }
        .task(id: selected?.id) {
            if let id = selected?.id { await strategy.arm(id) }
        }
        .task { await services.dashboard.pollServices(lifecycle: services.lifecycle) }
        // Warm the portfolio sheet once there is more than one account to
        // switch between, so its first opening already has figures.
        .task(id: accounts?.count ?? 0) {
            if !portfoliosPrefetched, let accounts, accounts.count > 1 {
                portfoliosPrefetched = true
                portfolios.refreshDetached(accounts)
            }
        }
        // Hero chart: live at the range's cadence; restarts on a range or
        // account switch.
        .task(id: portfolioScope.map { "\($0.brokerageId)|\($0.chart.range)" }) {
            guard let scope = portfolioScope else { return }
            await scope.chart.poll(lifecycle: services.lifecycle)
        }
        .task(id: portfolioScope?.brokerageId) {
            guard let scope = portfolioScope else { return }
            await scope.holdings.poll(lifecycle: services.lifecycle)
        }
        .task(id: portfolioScope.map { "\($0.brokerageId)|\(feed.pnlMode.rawValue)" }) {
            guard let scope = portfolioScope else { return }
            await scope.holdings.showSparks(feed.pnlMode.sparkRange)
        }
        // Insights + Market.
        .task(id: liveScope?.brokerageId) {
            guard let scope = liveScope else { return }
            let id = scope.brokerageId
            async let account: Void = scope.insights.load(holdings: scope.holdings)
            async let market: Void = feed.loadMarket()
            async let risk: Void = feed.loadRisk(id)
            async let sectors: Void = feed.loadSectorAllocation(id, holdings: { try await scope.holdings.currentHoldings() })
            _ = await (account, market, risk, sectors)
        }
        .task(id: liveScope?.brokerageId) {
            guard let id = liveScope?.brokerageId else { return }
            await feed.pollDayChange(id, lifecycle: services.lifecycle)
        }
        // Kalshi glance: fetched once per account.
        .task(id: kalshiAccount?.id) {
            guard let id = kalshiAccount?.id else { return }
            await kalshi.load(id, repository: services.kalshiRepository)
        }
        .onChange(of: selected?.id, initial: true) { _, id in
            guard let id else {
                scope = nil
                return
            }
            if scope?.brokerageId != id {
                let services = services
                // Stamped through the generation guard: a scope built
                // before a sign-out or server change cannot stamp the new session.
                let generation = services.dashboard.currentGeneration
                scope = DashboardAccountScope(
                    brokerageId: id,
                    client: { [unowned services] in services.apiClient },
                    onPortfolioUpdated: { [weak dashboard = services.dashboard] in
                        dashboard?.stampPortfolioUpdated(startedGeneration: generation)
                    }
                )
            }
        }
        .sheet(isPresented: $showPortfolios) {
            if let accounts, let selected {
                DashboardPortfolioSheet(
                    accounts: accounts,
                    selectedId: selected.id,
                    portfolios: portfolios,
                    heroSummary: liveScope?.chart.daySummary,
                    onSelect: { services.selectedAccount.select($0) }
                )
            }
        }
        .sheet(item: $browserLink) { link in
            DashboardSafariView(url: link.url).ignoresSafeArea()
        }
        .sheet(isPresented: $startAgentSheet) {
            DashboardStartAgentSheet { specialRequest in
                let repo = services.dashboardRepository
                Task {
                    await services.dashboard.run("ai_backtest_engine") {
                        try await repo.controlAgent(running: true, specialRequest: specialRequest)
                    }
                }
            }
            .presentationDetents([.medium])
            .presentationDragIndicator(.visible)
        }
    }

    // MARK: Portfolio

    @ViewBuilder
    private var portfolio: some View {
        switch services.dashboard.brokerages {
        case .loading:
            DashboardPortfolioSkeleton()
        case .failed:
            DashboardSearchRow()
            Section {
                ErrorRow(message: services.dashboard.brokerages.errorMessage ?? "") {
                    Task { await services.dashboard.loadBrokerages() }
                }
            }
        case .loaded(let accounts):
            if accounts.isEmpty {
                DashboardSearchRow()
                Section {
                    EmptyState(
                        systemImage: Symbol.named("account_balance"),
                        title: "No brokerages linked.",
                        subtitle: "Link a brokerage to see your portfolio here.",
                        actionLabel: "Link a Brokerage",
                        onAction: { services.router.push(.brokerages) }
                    )
                    .listRowBackground(Color.clear)
                }
            } else if let selected, let scope = liveScope {
                DashboardPortfolioSections(
                    accounts: accounts,
                    selected: selected,
                    scope: scope,
                    feed: feed,
                    onSwitchAccount: { showPortfolios = true }
                )
            } else {
                DashboardPortfolioSkeleton()
            }
        }
    }

    // MARK: Re-run onboarding

    private var onboardingSection: some View {
        Section {
            NavigationLink(value: Route.onboarding) {
                Label("Re-run onboarding", systemImage: Symbol.named("replay"))
            }
        } footer: {
            Text("Walk through the welcome flow again to add another model, link a brokerage, or spin up a new instance.")
        }
    }

    // MARK: Refresh

    private func refreshAll() async {
        await services.dashboard.loadBrokerages()
        async let svc: Void = services.dashboard.refreshNow()
        guard let scope else {
            _ = await svc
            return
        }
        let id = scope.brokerageId
        async let holdings: Void = scope.holdings.reload()
        async let chart: Void = scope.chart.refresh()
        async let strategyReload: Void = strategy.reload(id)
        _ = await (svc, holdings, chart, strategyReload)
        async let insights: Void = scope.insights.load(holdings: scope.holdings, force: true)
        async let feedReload: Void = feed.refreshAll(id, holdings: { try await scope.holdings.currentHoldings() })
        _ = await (insights, feedReload)
    }
}

/// The portfolio's loading shape: the hero's layout, redacted, with the
/// live search button already in its place.
struct DashboardPortfolioSkeleton: View {
    var body: some View {
        Section {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .center, spacing: 12) {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Alpaca Live")
                            .font(.subheadline.weight(.semibold))
                        HeroValueHeader("$0,000.00", change: "+$00.00 (+0.00%)", direction: .flat, status: "Markets Open")
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .redacted(reason: .placeholder)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("Loading")
                    DashboardSearchButton()
                }
                Skeleton(height: DashboardPortfolioMetrics.chartHeight, radius: 8)
                    .redacted(reason: .placeholder)
                    .accessibilityHidden(true)
            }
            .padding(.bottom, 4)
            .listRowBackground(Color.clear)
            .listRowInsets(.top, 0)
        }
    }
}

/// A dashboard group heading over the first section of a group ("Holdings",
/// "Market", "Strategy", "Services"): `.title3` bold in the primary colour,
/// the way Health and Fitness head their summary groups. A section that also
/// has its own title shows it under the group, in the system header style.
struct DashboardGroupHeader<Accessory: View>: View {
    let group: String?
    let title: String?
    private let accessory: Accessory

    init(group: String?, title: String? = nil, @ViewBuilder accessory: () -> Accessory) {
        self.group = group
        self.title = title
        self.accessory = accessory()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let group {
                HStack(alignment: .center, spacing: 8) {
                    // `Color.primary`, not `.primary`: a header's base style is
                    // secondary, and the hierarchical `.primary` would resolve
                    // to it.
                    Text(group)
                        .font(.title3.bold())
                        .foregroundStyle(Color.primary)
                        .accessibilityAddTraits(.isHeader)
                    if title == nil {
                        Spacer(minLength: 8)
                        accessory
                    }
                }
            }
            if let title {
                HStack(alignment: .center, spacing: 8) {
                    Text(title)
                        .accessibilityAddTraits(.isHeader)
                    Spacer(minLength: 8)
                    accessory
                }
            }
        }
        .textCase(nil)
    }
}

extension DashboardGroupHeader where Accessory == EmptyView {
    init(group: String?, title: String? = nil) {
        self.init(group: group, title: title) { EmptyView() }
    }
}
