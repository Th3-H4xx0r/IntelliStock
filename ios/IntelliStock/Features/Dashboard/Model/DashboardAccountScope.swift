import Foundation
import Observation

/// Everything the dashboard keys by the selected account: the holdings
/// poller, the hero chart, the per-account insights and the orders. The Dart
/// providers behind them were autoDispose families (keyed by brokerage id),
/// so a new scope is built whenever the selected account changes.
@Observable
final class DashboardAccountScope {
    let brokerageId: String
    let holdings: AccountHoldingsModel
    let chart: DashboardPortfolioChartModel
    let insights: DashboardAccountInsightsModel
    /// Built for every account; polled only for a stock account
    /// (`BrokerageAccount.hasOrderBook`).
    let orders: AccountOrdersModel

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
        orders = AccountOrdersModel(
            brokerageId: brokerageId,
            repository: { DashboardRepository(client: client()) },
            trade: { TradeRepository(client: client()) }
        )
    }
}
