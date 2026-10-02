import Testing
import UIKit
@testable import IntelliStock

/// Every go_router path in `router.dart` maps to one `Route`, and the
/// router's push/go semantics match `context.push` / `context.go`.
struct RouteTests {
    @Test(arguments: [
        ("/backtests", Route.backtests),
        ("/backtests/b1", .backtest("b1")),
        ("/backtests/b1/playback", .backtestPlayback("b1")),
        ("/kalshi/instances/k1", .kalshiInstance("k1")),
        ("/kalshi/instances/k1/backtest", .kalshiBacktest("k1")),
        ("/kalshi/backtests/kb1", .kalshiBacktestResult("kb1")),
        ("/instances/abc", .instance("abc")),
        ("/instances/abc/live", .liveTrading("abc")),
        ("/strategies/s1", .strategy("s1")),
        ("/brokerages", .brokerages),
        ("/crypto", .crypto),
        ("/crypto/instances/c1", .cryptoInstance("c1")),
        ("/search", .search),
        ("/stock/BRK.B", .stock(StockRoute(symbol: "BRK.B"))),
        ("/stock/BRK%2FB", .stock(StockRoute(symbol: "BRK/B"))),
        ("/agent-runs", .agentRuns),
        ("/nexus", .nexus),
        ("/learning", .learning),
        ("/models", .models),
        ("/token-usage", .tokenUsage),
        ("/settings", .settings),
        ("/settings/notifications", .notificationSettings),
        ("/connect", .connect),
        ("/onboarding", .onboarding),
    ])
    func parsesEveryGoRouterPath(path: String, route: Route) {
        #expect(Route(path: path) == route)
    }

    @Test func unknownPathsAndTabRootsAreNotRoutes() {
        #expect(Route(path: "/nope") == nil)
        #expect(Route(path: "/instances/a/b/c") == nil)
        #expect(Route(path: "/dashboard") == nil)
        #expect(Route(path: "") == nil)
    }

    @Test func tabRootsParse() {
        #expect(AppTab(rootPath: "/dashboard") == .dashboard)
        #expect(AppTab(rootPath: "/kalshi") == .kalshi)
        #expect(AppTab(rootPath: "/instances") == .instances)
        #expect(AppTab(rootPath: "/strategies") == .strategies)
        #expect(AppTab(rootPath: "/backtests") == nil)
    }

    @Test @MainActor func pushAppendsToTheCurrentTab() {
        let router = AppRouter()
        router.tab = .instances
        router.push(.instance("a"))
        router.push(.liveTrading("a"))
        #expect(router.stack(for: .instances) == [.instance("a"), .liveTrading("a")])
        #expect(router.stack(for: .dashboard).isEmpty)
    }

    @Test @MainActor func goToATabRootSelectsItAndPopsToRoot() {
        let router = AppRouter()
        router.tab = .kalshi
        router.push(.kalshiInstance("k"))
        router.go("/kalshi")
        #expect(router.tab == .kalshi)
        #expect(router.stack(for: .kalshi).isEmpty)
        router.go("/strategies")
        #expect(router.tab == .strategies)
    }

    @Test @MainActor func goToADetailReplacesTheCurrentStack() {
        let router = AppRouter()
        router.tab = .more
        router.push(.backtests)
        router.push(.backtest("old"))
        router.go("/backtests/new")
        #expect(router.stack(for: .more) == [.backtest("new")])
    }

    @Test @MainActor func openPushesParsedPathsAndIgnoresUnknown() {
        let router = AppRouter()
        router.open("/instances/x")
        #expect(router.stack(for: .dashboard) == [.instance("x")])
        router.open("/garbage")
        #expect(router.stack(for: .dashboard) == [.instance("x")])
        router.open("/strategies")
        #expect(router.tab == .strategies)
    }

    @Test @MainActor func popToRootClearsOneTab() {
        let router = AppRouter()
        router.push(.search)
        router.popToRoot(.dashboard)
        #expect(router.stack(for: .dashboard).isEmpty)
    }
}

struct SymbolTests {
    @Test(arguments: Symbol.materialNames)
    func everyMaterialNameMapsToARealSFSymbol(name: String) {
        let sf = Symbol.named(name)
        // Names that really are a plain circle may map to the fallback glyph.
        #expect(sf != Symbol.fallback || ["circle", "radio_button_unchecked"].contains(name), "unmapped: \(name)")
        #expect(UIImage(systemName: sf) != nil, "\(name) → \(sf) is not an SF Symbol")
    }

    @Test func unknownNamesFallBackToCircle() {
        #expect(Symbol.named("definitely_not_an_icon") == "circle")
        #expect(UIImage(systemName: Symbol.fallback) != nil)
    }

    @Test func outlinedAndRoundedSuffixesResolveToTheBaseName() {
        #expect(Symbol.named("delete_outline") == Symbol.named("delete"))
        #expect(Symbol.named("warning_amber_rounded") == Symbol.named("warning_amber"))
        #expect(Symbol.named("smart_toy_outlined") == Symbol.named("smart_toy"))
    }
}
