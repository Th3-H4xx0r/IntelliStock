import Foundation

/// The five tabs of the signed-in app. Flutter's shell had four branches plus
/// a "More" button that opened a sheet; here More is a real tab
/// (tab-bars.md: "Use a tab bar to support navigation, not to provide actions").
nonisolated enum AppTab: String, Hashable, CaseIterable, Sendable {
    case dashboard, kalshi, instances, strategies, more

    /// The go_router branch path of a tab root, e.g. `/kalshi`.
    init?(rootPath: String) {
        switch Route.pathOnly(rootPath) {
        case "/dashboard": self = .dashboard
        case "/kalshi": self = .kalshi
        case "/instances": self = .instances
        case "/strategies": self = .strategies
        default: return nil
        }
    }

    /// The go_router branch path of a tab root; More has none (it was a sheet).
    var rootPath: String? {
        switch self {
        case .dashboard: "/dashboard"
        case .kalshi: "/kalshi"
        case .instances: "/instances"
        case .strategies: "/strategies"
        case .more: nil
        }
    }
}

/// `StockScreenArgs` in `stock_screen.dart`: the symbol from the path plus
/// optional context passed as go_router `extra`.
nonisolated struct StockRoute: Hashable, Sendable {
    let symbol: String
    var position: AccountPosition?
    var brokerageId: String?
    var portfolioTotal: Double?

    init(symbol: String, position: AccountPosition? = nil, brokerageId: String? = nil, portfolioTotal: Double? = nil) {
        self.symbol = symbol
        self.position = position
        self.brokerageId = brokerageId
        self.portfolioTotal = portfolioTotal
    }
}

/// One case per pushable go_router route in `router.dart`. Tab roots are
/// `AppTab`s, not routes; `/login` is a gate, not a destination.
nonisolated enum Route: Hashable, Sendable {
    case backtests
    case backtest(String)
    case backtestPlayback(String)
    case kalshiInstance(String)
    case kalshiBacktest(String)
    case kalshiBacktestResult(String)
    case instance(String)
    case liveTrading(String)
    case strategy(String)
    case brokerages
    case crypto
    case cryptoInstance(String)
    case search
    case stock(StockRoute)
    case agentRuns
    case nexus
    case learning
    case models
    case tokenUsage
    case settings
    case notificationSettings
    /// `/connect` pushed from Settings to change the server URL.
    case connect
    /// `/onboarding` pushed from the dashboard's onboarding panel.
    case onboarding

    /// Parses a go_router location such as `/instances/abc/live`. Path
    /// parameters are percent-decoded, as go_router decodes them.
    init?(path: String) {
        // go_router matches the path only; `?query` and `#fragment` are not
        // part of the location's route.
        let parts = Route.pathOnly(path).split(separator: "/", omittingEmptySubsequences: true)
            .map { $0.removingPercentEncoding ?? String($0) }
        switch parts.count {
        case 1:
            switch parts[0] {
            case "backtests": self = .backtests
            case "brokerages": self = .brokerages
            case "crypto": self = .crypto
            case "search": self = .search
            case "agent-runs": self = .agentRuns
            case "nexus": self = .nexus
            case "learning": self = .learning
            case "models": self = .models
            case "token-usage": self = .tokenUsage
            case "settings": self = .settings
            case "connect": self = .connect
            case "onboarding": self = .onboarding
            default: return nil
            }
        case 2:
            let id = parts[1]
            switch parts[0] {
            case "backtests": self = .backtest(id)
            case "instances": self = .instance(id)
            case "strategies": self = .strategy(id)
            case "stock": self = .stock(StockRoute(symbol: id))
            case "settings" where id == "notifications": self = .notificationSettings
            default: return nil
            }
        case 3:
            switch (parts[0], parts[2]) {
            case ("backtests", "playback"): self = .backtestPlayback(parts[1])
            case ("instances", "live"): self = .liveTrading(parts[1])
            case ("kalshi", _) where parts[1] == "instances": self = .kalshiInstance(parts[2])
            case ("kalshi", _) where parts[1] == "backtests": self = .kalshiBacktestResult(parts[2])
            case ("crypto", _) where parts[1] == "instances": self = .cryptoInstance(parts[2])
            default: return nil
            }
        case 4:
            if parts[0] == "kalshi", parts[1] == "instances", parts[3] == "backtest" {
                self = .kalshiBacktest(parts[2])
            } else {
                return nil
            }
        default:
            return nil
        }
    }
}
