import SwiftUI

/// The signed-in shell — `AppShell` in `app_shell.dart`. Five tabs, each with
/// its own navigation stack bound to `AppRouter`, so detail routes push
/// inside the current tab and the tab bar stays visible
/// (`tab-bars.md › Best practices`). The symbols draw as outlines, like the
/// web's line icons. iOS 26 draws the bar itself as Liquid Glass and ignores
/// a custom bar appearance, so it is left as the system draws it.
///
/// The chat's entry is the tab bar's bottom accessory, a glass capsule above
/// the tabs (`ChatAccessoryView`), and `chatbotPresenter()` hosts the chat
/// sheet. Both live only here, so there is no chat entry outside the signed-in
/// shell, and none while locked (`LockView` replaces the shell).
struct MainTabView: View {
    @Environment(AppServices.self) private var services
    @Environment(\.scenePhase) private var scenePhase

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
        // Outline glyphs, as the web's icon set draws.
        .environment(\.symbolVariants, .none)
        // No chat bar over a pushed Onboarding, which hides the tab bar and
        // covers the shell as the Dart route did.
        .modifier(ShellChatAccessory(isEnabled: services.router.stack(for: services.router.tab).last != .onboarding))
        .chatbotPresenter()
        // Inside the authenticated shell: register for push once per sign-in.
        .task { await services.push.enable() }
        // "Update Available" when a newer build is published: checked as the
        // shell appears (launch, sign-in, unlock) and on every return to the
        // foreground.
        .appUpdatePresenter(services.appUpdates)
        .onChange(of: scenePhase, initial: true) { _, phase in
            services.appUpdates.scenePhaseChanged(to: phase)
        }
        .onDisappear { services.appUpdates.shellDisappeared() }
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
                .dsScreenBackground()
                .navigationDestination(for: Route.self) { RouteDestination(route: $0).dsScreenBackground() }
        }
    }
}

/// The "Ask IntelliStock" tab-bar accessory. `isEnabled:` is iOS 26.1+; on
/// 26.0 the accessory always shows.
private struct ShellChatAccessory: ViewModifier {
    let isEnabled: Bool

    func body(content: Content) -> some View {
        if #available(iOS 26.1, *) {
            content.tabViewBottomAccessory(isEnabled: isEnabled) { ChatAccessoryView() }
        } else {
            content.tabViewBottomAccessory { ChatAccessoryView() }
        }
    }
}
