import Foundation
import Observation

/// One account's equity and today's change, as the portfolio sheet shows it.
nonisolated struct DashboardAccountSummary: Hashable, Sendable {
    let equity: Double
    /// nil when there is no change to report (an empty history).
    let dayChange: Double?
    /// nil when the baseline is 0, as in `computeChange`.
    let dayChangePct: Double?

    init(equity: Double, dayChange: Double?, dayChangePct: Double?) {
        self.equity = equity
        self.dayChange = dayChange
        self.dayChangePct = dayChangePct
    }

    /// The hero's figures for a 1D history: the value it shows
    /// (`dashboardHeroValue`) and its change line (`computeChange`), so the
    /// sheet and the hero never disagree.
    init(history: PortfolioHistory) {
        let change = computeChange(history)
        self.init(equity: dashboardHeroValue(history), dayChange: change.abs, dayChangePct: change.pct)
    }

    /// A Kalshi account, as the Kalshi tab's "Portfolio value" reads it: the
    /// account value and its day change. The percent is against the value a
    /// day ago (`value - day_change`, the server's baseline).
    init(kalshi p: KalshiPortfolio) {
        let base = p.value - p.dayChange
        self.init(equity: p.value, dayChange: p.dayChange, dayChangePct: base != 0 ? (p.dayChange / base) * 100 : nil)
    }

    /// `+$65.37 (+1.12%)`, the hero's change format.
    var changeText: String { "\(fmtPnl(dayChange)) (\(fmtPct(dayChangePct)))" }

    /// The change line's colour, as the hero picks it (`abs ?? 0`).
    var direction: ChangeDirection { ChangeDirection(dayChange ?? 0) }
}

/// Where the portfolio sheet's figures come from, per account.
nonisolated enum DashboardPortfolios {
    /// One account's figures, fetched the way its own screen reads them:
    ///
    /// - **Kalshi:** `GET /brokerages/{id}/kalshi/portfolio`, the Kalshi
    ///   tab's "Portfolio value". The server's portfolio history answers
    ///   400 "Unsupported brokerage type" for Kalshi.
    /// - **Everything else:** the dashboard hero's own source,
    ///   `GET /brokerages/{id}/portfolio-history?range=1D`, re-based to local
    ///   midnight as the hero does. The server serves it for Alpaca accounts
    ///   and refuses any other type, which then reads as no data.
    static func summary(
        for account: BrokerageAccount,
        dashboard: DashboardRepository,
        kalshi: KalshiRepository,
        now: Date = Date()
    ) async throws -> DashboardAccountSummary {
        if account.brokerageType.lowercased() == "kalshi" {
            return DashboardAccountSummary(kalshi: try await kalshi.portfolio(account.id))
        }
        let history = try await dashboard.portfolioHistory(account.id, "1D")
        return DashboardAccountSummary(history: history.sinceLocalMidnight(now: now))
    }

    /// `summary(for:)` over two repositories, as the model's fetch.
    static func fetcher(
        dashboard: DashboardRepository,
        kalshi: KalshiRepository,
        now: @escaping @Sendable () -> Date = { Date() }
    ) -> DashboardPortfoliosModel.Fetch {
        { account in try await summary(for: account, dashboard: dashboard, kalshi: kalshi, now: now()) }
    }
}

/// The portfolio sheet's data: every account's equity and today's change,
/// fetched per account and in parallel from the source its own screen uses
/// (`DashboardPortfolios.summary(for:)`, about a second each against the
/// operator's server on 2026-10-02, where the old `GET /widget/accounts`
/// took about 18 s and covered only accounts an instance trades).
///
/// - **Rows fill as they arrive:** each account's result lands on its own;
///   until then that row is redacted.
/// - **One failure blanks nothing else:** a failed account keeps whatever it
///   showed before, or reads "—" when it never loaded.
/// - **Cached for the session:** the model lives with the dashboard, so a
///   reopened sheet shows the last figures at once while `refreshDetached`
///   fetches fresh ones in the background.
/// - **No double fetch:** an account already in flight is skipped, and a
///   refresh runs in its own task, so closing the sheet does not cancel it.
@Observable
final class DashboardPortfoliosModel {
    typealias Fetch = @Sendable (BrokerageAccount) async throws -> DashboardAccountSummary

    /// Brokerage id → figures.
    private(set) var summaries: [String: DashboardAccountSummary] = [:]
    /// Accounts whose fetch has settled at least once, successfully or not.
    private(set) var settled: Set<String> = []
    /// Accounts with a fetch in flight.
    private(set) var inFlight: Set<String> = []

    /// Read at the start of every refresh, so a server change reaches it.
    @ObservationIgnored private let fetcher: () -> Fetch

    init(fetcher: @escaping () -> Fetch) {
        self.fetcher = fetcher
    }

    var isLoading: Bool { !inFlight.isEmpty }

    /// Fetches every account not already in flight, all at once, applying
    /// each result as it arrives.
    func refresh(_ accounts: [BrokerageAccount]) async {
        var seen = Set<String>()
        let due = accounts.filter { !$0.id.isEmpty && !inFlight.contains($0.id) && seen.insert($0.id).inserted }
        guard !due.isEmpty else { return }
        inFlight.formUnion(due.map(\.id))
        let fetch = fetcher()
        await withTaskGroup(of: (String, Result<DashboardAccountSummary, any Error>).self) { group in
            for account in due {
                group.addTask {
                    do {
                        return (account.id, .success(try await fetch(account)))
                    } catch {
                        return (account.id, .failure(error))
                    }
                }
            }
            for await (id, result) in group {
                apply(id, result)
            }
        }
    }

    private func apply(_ id: String, _ result: Result<DashboardAccountSummary, any Error>) {
        inFlight.remove(id)
        switch result {
        case .success(let summary):
            summaries[id] = summary
            settled.insert(id)
        case .failure(let error) where error.isCancellation:
            // Not an answer: the row keeps waiting for the next refresh.
            break
        case .failure:
            // Keep whatever was showing; an account never loaded reads "—".
            settled.insert(id)
        }
    }

    /// `refresh(_:)` in a task of its own, so dismissing the sheet does not
    /// cancel the requests.
    func refreshDetached(_ accounts: [BrokerageAccount]) {
        Task { await refresh(accounts) }
    }

    /// The figures for `brokerageId`, or nil (still loading, or no data).
    func summary(_ brokerageId: String) -> DashboardAccountSummary? {
        summaries[brokerageId]
    }

    /// True while `brokerageId` has no figures and its first fetch has not
    /// settled: the row shows a redacted placeholder.
    func isPending(_ brokerageId: String) -> Bool {
        summaries[brokerageId] == nil && !settled.contains(brokerageId)
    }
}
