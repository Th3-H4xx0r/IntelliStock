import SwiftUI

/// The signed-in shell — `AppShell` in `app_shell.dart`. Five tabs, each with
/// its own navigation stack bound to `AppRouter`, so detail routes push
/// inside the current tab and the tab bar stays visible
/// (`tab-bars.md › Best practices`). The tab bar draws the symbols filled.
struct MainTabView: View {
    @Environment(AppServices.self) private var services

    var body: some View {
        @Bindable var router = services.router
        TabView(selection: $router.tab) {
            Tab("Dashboard", systemImage: Symbol.named("dashboard"), value: AppTab.dashboard) {
                TabStack(tab: .dashboard) { DashboardView() }
            }
            Tab("Kalshi", systemImage: Symbol.named("sports_soccer"), value: AppTab.kalshi) {
                TabStack(tab: .kalshi) { KalshiView() }
            }
            Tab("Instances", systemImage: Symbol.named("memory"), value: AppTab.instances) {
                TabStack(tab: .instances) { InstancesView() }
            }
            Tab("Strategies", systemImage: Symbol.named("schema"), value: AppTab.strategies) {
                TabStack(tab: .strategies) { StrategiesView() }
            }
            // Dart used the hamburger `menu`; Apple's More tab uses the ellipsis.
            Tab("More", systemImage: Symbol.named("more_horiz"), value: AppTab.more) {
                TabStack(tab: .more) { MoreTabView() }
            }
        }
        // Inside the authenticated shell: register for push once per sign-in.
        .task { await services.push.enable() }
    }
}

/// One tab's `NavigationStack`, bound to the router's stack for that tab.
private struct TabStack<Root: View>: View {
    let tab: AppTab
    @ViewBuilder let root: () -> Root

    @Environment(AppServices.self) private var services

    var body: some View {
        let router = services.router
        NavigationStack(path: Binding(
            get: { router.stack(for: tab) },
            set: { router.setStack($0, for: tab) }
        )) {
            root()
                .navigationDestination(for: Route.self) { RouteDestination(route: $0) }
        }
    }
}
