import Foundation
import Observation

/// One account's equity and today's change, as the portfolio sheet shows it.
nonisolated struct DashboardAccountSummary: Hashable, Sendable {
    let equity: Double
    let dayChange: Double
    let dayChangePct: Double

    /// `+$65.37 (+1.12%)`, the hero's change format.
    var changeText: String { "\(fmtPnl(dayChange)) (\(fmtPct(dayChangePct)))" }
}

/// The pure mapping behind the portfolio sheet.
nonisolated enum DashboardPortfolios {
    /// `GET /widget/accounts` reports one entry per INSTANCE, each carrying
    /// its brokerage's 1D value and day P&L. This keys them by brokerage
    /// through each instance's linkage (the first instance listed for a
    /// brokerage wins), so every dashboard account gets its own figures. An
    /// account no instance links to has no entry, and its row shows "—".
    static func summaries(
        widget: [DashboardWidgetAccount],
        instances: [Instance]
    ) -> [String: DashboardAccountSummary] {
        var brokerageOf: [String: String] = [:]
        for inst in instances {
            if let bid = brokerageId(of: inst), !bid.isEmpty, brokerageOf[inst.id] == nil {
                brokerageOf[inst.id] = bid
            }
        }
        var out: [String: DashboardAccountSummary] = [:]
        for account in widget {
            guard let bid = brokerageOf[account.id], out[bid] == nil else { continue }
            out[bid] = DashboardAccountSummary(
                equity: account.accountValue,
                dayChange: account.dayPnlAbs,
                dayChangePct: account.dayPnlPct
            )
        }
        return out
    }

    /// The instance's brokerage, resolved as the server's
    /// `_widget_brokerage_id` does: the nested `brokerage.brokerage_id`, then
    /// the top-level `brokerage_id`, then the nested `brokerage.id`.
    static func brokerageId(of inst: Instance) -> String? {
        if let nested = inst.brokerage?["brokerage_id"]?.string, !nested.isEmpty { return nested }
        if let top = inst.brokerageId, !top.isEmpty { return top }
        if let nestedId = inst.brokerage?["id"]?.string, !nestedId.isEmpty { return nestedId }
        return nil
    }
}

/// The portfolio sheet's data: each account's equity and today's change from
/// the read-only `GET /widget/accounts`, keyed by brokerage through
/// `GET /instances`. Additive and read-only: nothing on the dashboard depends
/// on it.
///
/// The widget endpoint assembles every instance's history and live state on
/// the server, so it is slow (about 18 s against the operator's server on
/// 2026-10-02). The dashboard starts one fetch when it opens with more than
/// one account, and the sheet starts another each time it appears. A fetch
/// runs in its own task, so closing the sheet does not cancel it, and a
/// second request while one is in flight is dropped. Loaded figures stay on
/// screen through a refetch and through a failed one.
@Observable
final class DashboardPortfoliosModel {
    typealias Fetch = () async throws -> (widget: [DashboardWidgetAccount], instances: [Instance])

    /// Brokerage id → figures. Missing means "no data" once `hasLoaded`.
    private(set) var summaries: [String: DashboardAccountSummary] = [:]
    /// True after the first fetch settles, successfully or not. Until then
    /// the sheet shows each value redacted.
    private(set) var hasLoaded = false
    private(set) var isLoading = false

    @ObservationIgnored private let fetch: Fetch

    init(fetch: @escaping Fetch) {
        self.fetch = fetch
    }

    /// Fetches now unless a fetch is already running.
    func refresh() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            let (widget, instances) = try await fetch()
            summaries = DashboardPortfolios.summaries(widget: widget, instances: instances)
        } catch where error.isCancellation {
            return
        } catch {
            // Keep whatever was showing; an account without figures reads "—".
        }
        hasLoaded = true
    }

    /// `refresh()` in a task of its own, so dismissing the sheet does not
    /// cancel the slow request.
    func refreshDetached() {
        guard !isLoading else { return }
        Task { await refresh() }
    }

    /// The figures for `brokerageId`, or nil (still loading, or no data).
    func summary(_ brokerageId: String) -> DashboardAccountSummary? {
        summaries[brokerageId]
    }
}
