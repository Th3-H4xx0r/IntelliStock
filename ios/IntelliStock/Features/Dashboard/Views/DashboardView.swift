import SwiftUI

/// The Dashboard tab root — `DashboardScreen` in `dashboard_screen.dart`.
///
/// The web dashboard's twin (`DashboardView.vue`): the page header (title,
/// date and market status, search, the account switcher), then Portfolio
/// (hero, chart, holdings, a stock account's orders) · Insights · Market ·
/// Strategy · Kalshi · Services · Re-run onboarding, as one `.dsList()`.
/// The navigation bar stays hidden; the header carries the title.
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

/// The dashboard's search button (`ui-icon-btn`), at the trailing end of
/// the page header: the Dashboard root hides its navigation bar.
struct DashboardSearchButton: View {
    @Environment(AppServices.self) private var services

    var body: some View {
        Button {
            services.router.push(DashboardTopActions.searchRoute)
        } label: {
            Image(systemName: Symbol.named("search"))
        }
        .buttonStyle(.dsIcon(side: 40))
        .accessibilityLabel(DashboardTopActions.searchLabel)
    }
}

/// The page header (`ui-page-title` + `ui-page-sub`): "Dashboard", today's
/// date and the market's status as a dot and a word, the search button, and
/// under them the account switcher once accounts are known.
private struct DashboardPageHeader<Switcher: View>: View {
    @ViewBuilder let switcher: () -> Switcher

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(DashboardTopActions.title)
                        .font(DS.Font.pageTitle)
                        .foregroundStyle(DS.Text.primary)
                        .accessibilityAddTraits(.isHeader)
                    DashboardLiveStatusChip()
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                DashboardSearchButton()
                    .padding(.trailing, -8)
            }
            switcher()
        }
    }
}

/// "Alpaca · Live", "Kalshi · Paper" — the web's `accountKind`.
func dashboardAccountKind(_ a: BrokerageAccount) -> String {
    let type = a.brokerageType.lowercased()
    let name = type == "alpaca" ? "Alpaca" : type == "kalshi" ? "Kalshi" : (a.brokerageType.isEmpty ? "Brokerage" : a.brokerageType.dsSentenceCased)
    return "\(name) · \(a.isPaper ? "Paper" : "Live")"
}

/// The condensed account switcher (the web header's): the account's name
/// and kind, its value and today's change, and a chevron. It opens the
/// Portfolios sheet; with one account it is a plain label.
private struct DashboardAccountSwitcher: View {
    let account: BrokerageAccount
    let summary: DashboardAccountSummary?
    let switchable: Bool
    let action: () -> Void

    var body: some View {
        let name = DashboardFormat.accountName(account)
        Button(action: action) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(name)
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.primary)
                        .lineLimit(1)
                    Text(dashboardAccountKind(account))
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                VStack(alignment: .trailing, spacing: 2) {
                    Text(summary.map { fmtMoney($0.equity) } ?? "—")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.primary)
                    Text(summary?.dayChangePct.map { fmtPct($0) } ?? " ")
                        .font(DS.Font.caption)
                        .foregroundStyle(dashboardChangeColor(summary?.dayChangePct))
                }
                .monospacedDigit()
                .lineLimit(1)
                if switchable {
                    Image(systemName: "chevron.down")
                        .font(.system(size: 14))
                        .foregroundStyle(DS.Text.muted)
                        .accessibilityHidden(true)
                }
            }
            .padding(.leading, 16)
            .padding(.trailing, 12)
            .frame(height: 56)
            .dsCardSurface(border: DS.Line.input)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!switchable)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Account, \(name)")
        .accessibilityHint(switchable ? "Shows your portfolios" : "")
    }
}

/// The web's `changeClass`: zinc-500 for no move, else gain or loss.
func dashboardChangeColor(_ pct: Double?) -> Color {
    guard let pct, abs(pct) >= 0.005 else { return DS.Text.muted }
    return pct > 0 ? DS.Palette.gain : DS.Palette.loss
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

    /// The portfolio scope when the selected account is a stock account,
    /// whose orders show under its holdings. Kalshi has no order book.
    private var ordersScope: DashboardAccountScope? {
        guard selected?.hasOrderBook == true else { return nil }
        return portfolioScope
    }

    private var kalshiAccount: BrokerageAccount? {
        (services.dashboard.brokeragesValue ?? []).first { $0.brokerageType == "kalshi" }
    }

    var body: some View {
        List {
            Section {
                DashboardPageHeader {
                    if let accounts, let selected, !accounts.isEmpty {
                        DashboardAccountSwitcher(
                            account: selected,
                            summary: liveScope?.chart.daySummary ?? portfolios.summary(selected.id),
                            switchable: accounts.count > 1,
                            action: { showPortfolios = true }
                        )
                    } else if services.dashboard.brokerages.value == nil, services.dashboard.brokerages.error == nil {
                        Skeleton(height: 56, radius: DS.Radius.card)
                    }
                }
                .dsClearRow(top: 8, bottom: 0)
            }
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
        .dsList()
        // The header starts right at the top of the safe area.
        .contentMargins(.top, 0, for: .scrollContent)
        .navigationTitle(DashboardTopActions.title)
        .navigationBarTitleDisplayMode(.inline)
        // No bar: the page header carries the title and the search button.
        .toolbar(.hidden, for: .navigationBar)
        // Rows scrolled up pass under a strip of the page colour, never
        // under the clock.
        .overlay(alignment: .top) {
            DS.Surface.page
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
        // Orders: every 30 s for a stock account; restarts on an account switch.
        .task(id: ordersScope?.brokerageId) {
            guard let scope = ordersScope else { return }
            await scope.orders.poll(lifecycle: services.lifecycle)
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
        // An order placed or canceled from a stock screen: reload that
        // account's holdings and orders when it is the one on screen.
        .onReceive(NotificationCenter.default.publisher(for: TradeEvents.ordersChanged)) { note in
            guard let id = note.object as? String, let scope = portfolioScope, scope.brokerageId == id else { return }
            let reloadsOrders = selected?.hasOrderBook == true
            Task {
                await scope.holdings.reload()
                if reloadsOrders { await scope.orders.reload() }
            }
        }
        // The Orders section's cancel confirm lives on the list, not on a
        // row that can scroll away.
        .tradeCancelAlert(Binding(
            get: { ordersScope?.orders.cancelPrompt },
            set: { ordersScope?.orders.cancelPrompt = $0 }
        )) { prompt in
            guard let orders = ordersScope?.orders, orders.brokerageId == prompt.brokerageId else { return }
            Task { await orders.cancel(prompt.order.id) }
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
            Section {
                ErrorRow(message: "Couldn't load brokerages: \(services.dashboard.brokerages.errorMessage ?? "")") {
                    Task { await services.dashboard.loadBrokerages() }
                }
                .dsClearRow(top: 24)
            }
        case .loaded(let accounts):
            if accounts.isEmpty {
                Section {
                    Card(padding: 0) {
                        DashboardNoBrokerages { services.router.push(.brokerages) }
                    }
                    .dsClearRow(top: 24)
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

    /// The web's closing row: a hairline, the sentence, and "Open onboarding".
    private var onboardingSection: some View {
        Section {
            VStack(alignment: .leading, spacing: 12) {
                DSDivider()
                VStack(alignment: .leading, spacing: 2) {
                    Text("Re-run onboarding")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.soft)
                    Text("Add another model, link a brokerage, or spin up a new instance")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.muted)
                }
                .padding(.top, 12)
                Button("Open onboarding") { services.router.push(.onboarding) }
                    .buttonStyle(.dsSecondary)
            }
            .dsClearRow(top: 0, bottom: 32)
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
        async let ordersReload: Void = reloadOrders(scope)
        _ = await (svc, holdings, chart, strategyReload, ordersReload)
        async let insights: Void = scope.insights.load(holdings: scope.holdings, force: true)
        async let feedReload: Void = feed.refreshAll(id, holdings: { try await scope.holdings.currentHoldings() })
        _ = await (insights, feedReload)
    }

    /// Orders reload only for a stock account.
    private func reloadOrders(_ scope: DashboardAccountScope) async {
        guard selected?.id == scope.brokerageId, selected?.hasOrderBook == true else { return }
        await scope.orders.reload()
    }
}

/// The portfolio's loading shape (the web's): static blocks where the
/// account line, the value and the chart will be.
struct DashboardPortfolioSkeleton: View {
    var body: some View {
        Section {
            VStack(alignment: .leading, spacing: 12) {
                Skeleton(width: 128, height: 16)
                Skeleton(width: 256, height: 44, radius: DS.Radius.small)
                Skeleton(height: DashboardPortfolioMetrics.chartHeight, radius: DS.Radius.control)
                    .padding(.top, 12)
            }
            .padding(.top, 24)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Loading")
            .dsClearRow()
        }
    }
}

/// No brokerage yet: one sentence and the one action (the web's words).
private struct DashboardNoBrokerages: View {
    let onLink: () -> Void

    var body: some View {
        VStack(spacing: 16) {
            Text("No brokerage linked yet. Link one to see its equity here")
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.secondary)
                .multilineTextAlignment(.center)
            Button("Link a brokerage", action: onLink)
                .buttonStyle(.dsPrimary)
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 24)
        .padding(.vertical, 48)
    }
}
