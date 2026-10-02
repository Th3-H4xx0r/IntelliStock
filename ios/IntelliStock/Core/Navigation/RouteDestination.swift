import SwiftUI

/// The single switch from `Route` to its screen. Every tab's
/// `NavigationStack` resolves pushes through it, so a route opened from any
/// tab shows the same view.
struct RouteDestination: View {
    let route: Route

    var body: some View {
        switch route {
        case .backtests: BacktestsView()
        case .backtest(let id): BacktestDetailView(id: id)
        case .backtestPlayback(let id): BacktestPlaybackView(id: id)
        case .kalshiInstance(let id): KalshiInstanceDetailView(instanceId: id)
        case .kalshiBacktest(let id): KalshiBacktestView(instanceId: id)
        case .kalshiBacktestResult(let id): KalshiBacktestResultView(backtestId: id)
        case .instance(let id): InstanceDetailView(instanceId: id)
        case .liveTrading(let id): LiveTradingView(instanceId: id)
        case .strategy(let id): StrategyDetailView(strategyId: id)
        case .brokerages: BrokeragesView()
        case .crypto: CryptoView()
        case .cryptoInstance(let id): CryptoInstanceDetailView(instanceId: id)
        case .search: SymbolSearchView()
        case .stock(let route): StockView(route: route)
        case .agentRuns: AgentRunsView()
        case .nexus: NexusView()
        case .learning: LearningView()
        case .models: ModelsView()
        case .tokenUsage: TokenUsageView()
        case .settings: SettingsView()
        case .notificationSettings: NotificationSettingsView()
        case .connect: ConnectView()
        case .onboarding: OnboardingView()
        }
    }
}
