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
/// so one instance lives in `AppServices` for the whole app.
@Observable
final class DashboardModel {
    /// Re-read on every call, so a client rebuilt for a new server URL is
    /// used without rebuilding the model.
    @ObservationIgnored private let repository: () -> DashboardRepository

    init(repository: @escaping () -> DashboardRepository) {
        self.repository = repository
    }

    // MARK: Services (DashboardServicesNotifier)

    /// The poll cadence (`interval()` in the Dart notifier).
    static let servicesInterval: Duration = .seconds(10)

    private(set) var services: Loadable<ServicesSnapshot> = .loading

    /// Force a refresh now (pull-to-refresh / manual button). A successful
    /// fetch replaces the snapshot; the previous one stays visible meanwhile.
    func refreshNow() async {
        // `DashboardRepository.services()` maps every endpoint failure to an
        // empty map, so the Dart `_refresh` catch never fires; nothing to
        // catch here either.
        services = .loaded(await repository().services())
    }

    /// Fetches now, then every `servicesInterval`, until the calling task is
    /// cancelled. Run it from the dashboard's `.task` keyed on the scene
    /// phase, so it pauses in the background and resumes on foreground, as
    /// `IntervalPoller` did. A fetch never stops the loop.
    func pollServices() async {
        await refreshNow()
        while !Task.isCancelled {
            try? await Task.sleep(for: Self.servicesInterval)
            if Task.isCancelled { break }
            await refreshNow()
        }
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
        do {
            let list = try await repository().brokerages()
            brokeragesValue = list
            brokerages = .loaded(list)
        } catch {
            brokerages = .failed(error)
        }
    }

    // MARK: Portfolio freshness (portfolioUpdatedAtProvider)

    /// Wall-clock time of the last *successful* portfolio-history fetch on
    /// the dashboard. Stamped by the history loader; read by the freshness
    /// label.
    var portfolioUpdatedAt: Date?
}
