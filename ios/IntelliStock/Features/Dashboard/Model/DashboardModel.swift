import Foundation
import Observation

/// The dashboard's shared state, ported from
/// features/dashboard/application/dashboard_controller.dart:
///
/// - `services` + `refreshNow()` + `pollServices()` ← `DashboardServicesNotifier`
///   (a `PollingNotifier` on a 10 s cadence);
/// - `busy` + `isBusy(_:)` + `run(_:_:)` ← `EngineBusyNotifier`;
/// - `brokerages` + `loadBrokerages()` ← `BrokeragesNotifier`
///   (`ref.invalidate(brokeragesProvider)` → `loadBrokerages()`);
/// - `portfolioUpdatedAt` ← `portfolioUpdatedAtProvider`.
///
/// The brokerage list is read by the dashboard, Kalshi and insights sections,
/// so one instance lives in `AppServices`. It is session-scoped:
/// `AppServices` calls `reset()` on sign-out and on a server change, so one
/// server's accounts and engines never show (or get sent) to another.
///
/// A cancelled request (its view went away) never changes the state; a
/// response that arrives after `reset()` is dropped.
@Observable
final class DashboardModel {
    /// Re-read on every call, so a client rebuilt for a new server URL is
    /// used without rebuilding the model.
    @ObservationIgnored private let repository: () -> DashboardRepository

    /// Bumped by `reset()`; a fetch that started under an older generation
    /// discards its result.
    @ObservationIgnored private var generation = 0

    init(repository: @escaping () -> DashboardRepository) {
        self.repository = repository
    }

    /// Back to the freshly-built state: loading, nothing busy, no cached
    /// brokerages. In-flight fetches are ignored when they land.
    func reset() {
        generation += 1
        services = .loading
        busy = []
        brokerages = .loading
        brokeragesValue = nil
        portfolioUpdatedAt = nil
    }

    // MARK: Services (DashboardServicesNotifier)

    /// The poll cadence (`interval()` in the Dart notifier).
    static let servicesInterval: Duration = .seconds(10)

    private(set) var services: Loadable<ServicesSnapshot> = .loading

    /// Force a refresh now (pull-to-refresh / manual button). A successful
    /// fetch replaces the snapshot; the previous one stays visible meanwhile.
    /// A cancelled fetch, or one overtaken by `reset()`, changes nothing.
    func refreshNow() async {
        // `fetchServices()` maps every endpoint failure to an empty map, so the
        // Dart `_refresh` catch never fires; it throws only on cancellation.
        let started = generation
        guard let snapshot = try? await repository().fetchServices(), started == generation else { return }
        services = .loaded(snapshot)
    }

    /// Fetches now, then every `servicesInterval` until the calling task is
    /// cancelled, pausing while `lifecycle` reports the background and
    /// resuming on foreground (`PollingNotifier` + `IntervalPoller`). Run it
    /// once from the dashboard's `.task`:
    ///
    ///     .task { await services.dashboard.pollServices(lifecycle: services.lifecycle) }
    ///
    /// Do not key the task on the scene phase — the loop already follows it.
    func pollServices(lifecycle: AppLifecycle?, sleep: @escaping PollingSleep = realPollingSleep) async {
        await refreshNow()
        let loop = PollingLoop(interval: { Self.servicesInterval }, sleep: sleep) { [weak self] in
            await self?.refreshNow()
        }
        await loop.run(lifecycle: lifecycle)
    }

    /// `pollServices(lifecycle:)` without lifecycle pausing, kept for source
    /// compatibility. Prefer passing `services.lifecycle`.
    func pollServices() async {
        await pollServices(lifecycle: nil)
    }

    // MARK: Busy / in-flight tracking per engine (EngineBusyNotifier)

    /// Engine ids with an action in flight: `price_engine`, `discover_engine`,
    /// `ai_backtest_engine`, `daily_digest_engine`, `nexus_graph_engine`.
    private(set) var busy: Set<String> = []

    func isBusy(_ id: String) -> Bool { busy.contains(id) }

    /// Runs `action`, marks `id` busy for the duration, then refreshes
    /// services so the new status appears immediately. Errors are swallowed,
    /// as the Dart notifier did; a second call for a busy id is ignored.
    func run(_ id: String, _ action: () async throws -> Void) async {
        if busy.contains(id) { return }
        busy.insert(id)
        defer { busy.remove(id) }
        do {
            try await action()
            await refreshNow()
        } catch {
            // Surface errors via the services state; keep going.
        }
    }

    // MARK: Brokerages (BrokeragesNotifier)

    private(set) var brokerages: Loadable<[BrokerageAccount]> = .loading

    /// The last successfully loaded list — Riverpod's `valueOrNull`, which
    /// survives a refetch and a failed refetch.
    private(set) var brokeragesValue: [BrokerageAccount]?

    /// Loads the brokerage account list. Call it on first appearance and to
    /// retry or pull-to-refresh (`ref.invalidate(brokeragesProvider)`).
    /// Loaded data stays visible while the refetch runs.
    func loadBrokerages() async {
        let started = generation
        do {
            let list = try await repository().brokerages()
            guard started == generation else { return }
            brokeragesValue = list
            brokerages = .loaded(list)
        } catch where error.isCancellation {
            // The caller went away; keep what is showing.
        } catch {
            guard started == generation else { return }
            brokerages = .failed(error)
        }
    }

    // MARK: Portfolio freshness (portfolioUpdatedAtProvider)

    /// Wall-clock time of the last *successful* portfolio-history fetch on
    /// the dashboard. Stamped by the history loader; read by the freshness
    /// label.
    var portfolioUpdatedAt: Date?
}
