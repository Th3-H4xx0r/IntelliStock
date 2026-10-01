import Foundation

/// Every feature repository, built on the current `apiClient` (the Dart
/// `xRepositoryProvider`s, which `ref.watch(apiClientProvider)`). Each read
/// builds a fresh value over the current client, so a client rebuilt for a
/// new server URL is picked up with no extra wiring. Repositories are cheap
/// `Sendable` structs holding only the client.
///
/// `PushRepository` belongs to the core agent and is not listed here.
extension AppServices {
    var agentRepository: AgentRepository { AgentRepository(client: apiClient) }
    var authRepository: AuthRepository { AuthRepository(client: apiClient) }
    var backtestRepository: BacktestRepository { BacktestRepository(client: apiClient) }
    var brokerageRepository: BrokerageRepository { BrokerageRepository(client: apiClient) }
    var chatbotRepository: ChatbotRepository { ChatbotRepository(client: apiClient) }
    var cryptoRepository: CryptoRepository { CryptoRepository(client: apiClient) }
    var dashboardRepository: DashboardRepository { DashboardRepository(client: apiClient) }
    var instanceRepository: InstanceRepository { InstanceRepository(client: apiClient) }
    var kalshiRepository: KalshiRepository { KalshiRepository(client: apiClient) }
    var learningRepository: LearningRepository { LearningRepository(client: apiClient) }
    var liveRepository: LiveRepository { LiveRepository(client: apiClient) }
    var modelRepository: ModelRepository { ModelRepository(client: apiClient) }
    var nexusRepository: NexusRepository { NexusRepository(client: apiClient) }
    var notificationPrefsRepository: NotificationPrefsRepository { NotificationPrefsRepository(client: apiClient) }
    var onboardingRepository: OnboardingRepository { OnboardingRepository(client: apiClient) }
    var strategyRepository: StrategyRepository { StrategyRepository(client: apiClient) }
    var swingRepository: SwingRepository { SwingRepository(client: apiClient) }
    var symbolSearchRepository: SymbolSearchRepository { SymbolSearchRepository(client: apiClient) }
    var tokenUsageRepository: TokenUsageRepository { TokenUsageRepository(client: apiClient) }
}
