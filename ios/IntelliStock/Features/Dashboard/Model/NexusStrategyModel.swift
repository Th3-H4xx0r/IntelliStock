import Foundation
import Observation

/// The nexus strategy telemetry for the dashboard's Strategy section, ported
/// from features/dashboard/application/nexus_strategy_controller.dart.
///
/// The Dart providers were keep-alive families: each endpoint was fetched
/// once per account per app session and reused (the data changes on the
/// bot's run cadence). The same caches live here, per account; unlike Dart,
/// pull-to-refresh (`reload`) re-fetches them. Missing entries mean "not
/// loaded yet". Every fetch is never-throwing, with the Dart fallbacks.
@Observable
final class NexusStrategyModel {
    private(set) var trends: [String: NexusTrendsView] = [:]
    private(set) var backfill: [String: [BackfillItem]] = [:]
    private(set) var discovered: [String: [DiscoveredStock]] = [:]
    private(set) var contexts: [String: [TradeRationale]] = [:]
    private(set) var outcomes: [String: OutcomeStats] = [:]
    private(set) var watchlist: [String: WatchlistSummary] = [:]
    /// False until the deferral has passed; the section stays hidden until then.
    private(set) var armed = false

    /// The deferral before the (optional, read-only) telemetry fetches.
    static let armDelay: Duration = .milliseconds(900)

    @ObservationIgnored private let repository: () -> DashboardRepository

    init(repository: @escaping () -> DashboardRepository) {
        self.repository = repository
    }

    /// Waits out the first-load deferral (once), then loads `id`.
    func arm(_ id: String, sleep: PollingSleep = realPollingSleep) async {
        if !armed {
            do { try await sleep(Self.armDelay) } catch { return }
            armed = true
        }
        await load(id)
    }

    /// Fetches every card's data for `id` that is not cached yet, in parallel.
    func load(_ id: String) async {
        let repo = repository()
        let (needT, needB, needD) = (trends[id] == nil, backfill[id] == nil, discovered[id] == nil)
        let (needC, needO, needW) = (contexts[id] == nil, outcomes[id] == nil, watchlist[id] == nil)
        async let t: NexusTrendsView? = needT ? Self.trends(repo, id) : nil
        async let b: [BackfillItem]? = needB ? ((try? await repo.backfillQueue(id)) ?? []) : nil
        async let d: [DiscoveredStock]? = needD ? ((try? await repo.discoveredStocks(id)) ?? []) : nil
        async let c: [TradeRationale]? = needC ? ((try? await repo.tradeContexts(id)) ?? []) : nil
        async let o: OutcomeStats? = needO ? ((try? await repo.nexusOutcomes(id)) ?? OutcomeStats(json: [:])) : nil
        async let w: WatchlistSummary? = needW ? ((try? await repo.momentumWatchlist(id)) ?? WatchlistSummary(json: [:])) : nil
        let (tv, bv, dv, cv, ov, wv) = await (t, b, d, c, o, w)
        // These caches are stale-forever: never store a cancelled load's empties.
        if Task.isCancelled { return }
        if let tv { trends[id] = tv }
        if let bv { backfill[id] = bv }
        if let dv { discovered[id] = dv }
        if let cv { contexts[id] = cv }
        if let ov { outcomes[id] = ov }
        if let wv { watchlist[id] = wv }
    }

    /// Drops `id`'s caches and fetches them again (pull-to-refresh).
    func reload(_ id: String) async {
        let repo = repository()
        async let t = Self.trends(repo, id)
        async let b = (try? await repo.backfillQueue(id)) ?? []
        async let d = (try? await repo.discoveredStocks(id)) ?? []
        async let c = (try? await repo.tradeContexts(id)) ?? []
        async let o = (try? await repo.nexusOutcomes(id)) ?? OutcomeStats(json: [:])
        async let w = (try? await repo.momentumWatchlist(id)) ?? WatchlistSummary(json: [:])
        let (tv, bv, dv, cv, ov, wv) = await (t, b, d, c, o, w)
        if Task.isCancelled { return }
        trends[id] = tv
        backfill[id] = bv
        discovered[id] = dv
        contexts[id] = cv
        outcomes[id] = ov
        watchlist[id] = wv
    }

    /// Whether any card has data — the Strategy section (header included)
    /// renders only then.
    func anyData(_ id: String) -> Bool {
        trends[id]?.isEmpty == false
            || backfill[id]?.isEmpty == false
            || discovered[id]?.isEmpty == false
            || contexts[id]?.isEmpty == false
            || outcomes[id]?.isEmpty == false
            || watchlist[id]?.isEmpty == false
    }

    /// `nexusTrendsProvider`: active (limit 30) + recently ended (limit 6),
    /// together; any failure → an empty view.
    nonisolated private static func trends(_ repo: DashboardRepository, _ id: String) async -> NexusTrendsView {
        do {
            async let active = repo.nexusTrends(id, status: "active", limit: 30)
            async let ended = repo.nexusTrends(id, status: "ended", limit: 6)
            return NexusTrendsView(active: try await active, recentlyEnded: try await ended)
        } catch {
            return NexusTrendsView(active: [], recentlyEnded: [])
        }
    }
}
