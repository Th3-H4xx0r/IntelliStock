import SwiftUI

/// The Dashboard tab root — `DashboardScreen` in `dashboard_screen.dart`.
///
/// Sections in Dart's order: Portfolio (hero, chart, holdings) · Insights ·
/// Strategy · Kalshi · Services · Re-run onboarding. A non-lazy stack keeps
/// every section built while scrolling, so each card loads once on entry
/// (Dart's `cacheExtent: 10000`). No backdrop gradient: the plain grouped
/// background (operator rule).
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
    /// The tab root's title. The hero itself carries no `Portfolio` heading.
    static let title = "Dashboard"
}

private struct DashboardContent: View {
    let services: AppServices

    @State private var feed: DashboardFeedModel
    @State private var strategy: NexusStrategyModel
    @State private var scope: DashboardAccountScope?
    @State private var widgetSynced = false

    init(services: AppServices) {
        self.services = services
        _feed = State(initialValue: DashboardFeedModel(
            loader: { [unowned services] in DashboardInsightsLoader(client: services.apiClient) }
        ))
        _strategy = State(initialValue: NexusStrategyModel(
            repository: { [unowned services] in services.dashboardRepository }
        ))
    }

    private var accounts: [BrokerageAccount]? { services.dashboard.brokerages.value }

    /// The account the hero shows: the stored selection when present, else
    /// the first account.
    private var selected: BrokerageAccount? {
        accounts.flatMap { DashboardFormat.resolveSelected($0, services.selectedAccount.selectedId) }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 32) {
                portfolio
                if let scope, let selected, scope.brokerageId == selected.id {
                    DashboardInsightsSection(scope: scope, feed: feed)
                    DashboardStrategySection(brokerageId: selected.id, model: strategy)
                }
                KalshiDashboardCard()
                DashboardServicesSection()
                onboardingPanel
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .padding(.bottom, 24)
        }
        .background(DS.Surface.canvas)
        .navigationTitle(DashboardTopActions.title)
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    services.router.push(DashboardTopActions.searchRoute)
                } label: {
                    Image(systemName: Symbol.named("search"))
                }
                .accessibilityLabel(DashboardTopActions.searchLabel)
            }
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
        .task {
            // TODO(merge): pollServices(lifecycle:) once feat/native-ios-app has it.
            await services.dashboard.pollServices()
        }
        .onChange(of: selected?.id, initial: true) { _, id in
            guard let id else {
                scope = nil
                return
            }
            if scope?.brokerageId != id {
                let services = services
                scope = DashboardAccountScope(
                    brokerageId: id,
                    client: { [unowned services] in services.apiClient },
                    onPortfolioUpdated: { [weak dashboard = services.dashboard] in dashboard?.portfolioUpdatedAt = Date() }
                )
            }
        }
    }

    // MARK: Portfolio

    @ViewBuilder
    private var portfolio: some View {
        switch services.dashboard.brokerages {
        case .loading:
            DashboardPortfolioSkeleton()
        case .failed:
            ErrorRow(message: services.dashboard.brokerages.errorMessage ?? "") {
                Task { await services.dashboard.loadBrokerages() }
            }
        case .loaded(let accounts):
            if accounts.isEmpty {
                EmptyState(
                    systemImage: Symbol.named("account_balance"),
                    title: "No brokerages linked.",
                    subtitle: "Link a brokerage to see your portfolio here.",
                    actionLabel: "Link a Brokerage",
                    onAction: { services.router.push(.brokerages) }
                )
            } else if let selected, let scope, scope.brokerageId == selected.id {
                DashboardPortfolioSection(
                    accounts: accounts,
                    selected: selected,
                    scope: scope,
                    feed: feed
                )
            } else {
                DashboardPortfolioSkeleton()
            }
        }
    }

    // MARK: Re-run onboarding

    private var onboardingPanel: some View {
        Card {
            HStack(spacing: 16) {
                VStack(alignment: .leading, spacing: 6) {
                    Label {
                        Text("Re-run onboarding")
                            .font(.subheadline.weight(.semibold))
                    } icon: {
                        Image(systemName: Symbol.named("replay"))
                            .foregroundStyle(.tint)
                    }
                    Text("Walk through the welcome flow again to add another model, link a brokerage, or spin up a new instance.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Button {
                    services.router.push(.onboarding)
                } label: {
                    Label("Open", systemImage: Symbol.named("arrow_forward"))
                        .labelStyle(.titleAndIcon)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
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

/// The portfolio skeleton: the hero's shape, redacted.
struct DashboardPortfolioSkeleton: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Skeleton.line(width: 120, height: 12)
            Skeleton(width: 200, height: 40, radius: 8)
            Skeleton.line(width: 120, height: 13)
            Skeleton(height: 30, radius: 8)
            Skeleton(height: 224, radius: 8)
        }
        .accessibilityLabel("Loading")
    }
}
