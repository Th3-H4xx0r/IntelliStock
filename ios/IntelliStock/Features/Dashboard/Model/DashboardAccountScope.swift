import Foundation
import Observation

/// Everything the dashboard keys by the selected account: the holdings
/// poller, the hero chart and the per-account insights. The Dart providers
/// behind them were autoDispose families (keyed by brokerage id), so a new
/// scope is built whenever the selected account changes.
@Observable
final class DashboardAccountScope {
    let brokerageId: String
    let holdings: AccountHoldingsModel
    let chart: DashboardPortfolioChartModel
    let insights: DashboardAccountInsightsModel

    init(
        brokerageId: String,
        client: @escaping () -> ApiClient,
        onPortfolioUpdated: @escaping () -> Void
    ) {
        self.brokerageId = brokerageId
        holdings = AccountHoldingsModel(
            brokerageId: brokerageId,
            repository: { DashboardRepository(client: client()) },
            live: { LiveRepository(client: client()) }
        )
        chart = DashboardPortfolioChartModel(
            accountId: brokerageId,
            fetch: { try await DashboardRepository(client: client()).portfolioHistory($0, $1) },
            onUpdated: onPortfolioUpdated
        )
        insights = DashboardAccountInsightsModel(
            brokerageId: brokerageId,
            loader: { DashboardInsightsLoader(client: client()) }
        )
    }
}
