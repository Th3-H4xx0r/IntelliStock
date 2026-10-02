import Foundation
import Observation

// Ported from features/instances/application/instances_controller.dart (the
// list half) and pinned_instances_controller.dart.

nonisolated enum InstanceFilter: Hashable, Sendable, CaseIterable {
    case all, user, ai
}

/// The list screen's state — `InstancesState`.
nonisolated struct InstancesState: Hashable, Sendable {
    var instances: [Instance] = []
    var filter: InstanceFilter = .all
    var busyIds: Set<String> = []
    var errorMessage: String?

    var filtered: [Instance] {
        if filter == .all { return instances }
        let target = filter == .ai ? "ai" : "user"
        return instances.filter { $0.createdBy == target }
    }

    var allCount: Int { instances.count }
    var userCount: Int { instances.filter { $0.createdBy != "ai" }.count }
    var aiCount: Int { instances.filter { $0.createdBy == "ai" }.count }
}

/// The instances list, polled every 30 s — `InstancesController` (a
/// `PollingNotifier`). A failed fetch puts the screen in its error state
/// (with Retry), as the Dart `AsyncError` did.
@Observable
final class InstancesModel {
    static let interval: Duration = .seconds(30)

    private(set) var state: Loadable<InstancesState> = .loading {
        didSet { if let value = state.value { last = value } }
    }

    @ObservationIgnored private let repository: () -> InstanceRepository
    /// The last good state. Riverpod kept the previous value through an
    /// error, so the next fetch starts from it: the User/AI filter, busy
    /// flags and error banner survive a failed poll or a Retry.
    @ObservationIgnored private var last = InstancesState()

    init(repository: @escaping () -> InstanceRepository) {
        self.repository = repository
    }

    var value: InstancesState? { state.value }

    /// `fetch`: the list, keeping filter/busy/error from the current value.
    private func fetch() async throws -> InstancesState {
        let list = try await repository().listInstances()
        var next = state.value ?? last
        next.instances = list
        return next
    }

    /// `refreshNow` / `_refresh`: success replaces the state; failure is the
    /// error state.
    func refreshNow() async {
        do {
            state = .loaded(try await fetch())
        } catch {
            if !tradingIsCancellation(error) { state = .failed(error) }
        }
    }

    /// Retry (`ref.invalidate`): back to loading, then fetch.
    func reload() async {
        state = .loading
        await refreshNow()
    }

    /// First fetch (unless loaded), then every 30 s, pausing in the
    /// background.
    func poll(lifecycle: AppLifecycle?, sleep: @escaping PollingSleep = realPollingSleep) async {
        if state.value == nil { await refreshNow() }
        await PollingLoop(interval: { Self.interval }, sleep: sleep) { [weak self] in
            await self?.refreshNow()
        }
        .run(lifecycle: lifecycle)
    }

    func setFilter(_ f: InstanceFilter) {
        guard var value = state.value else { return }
        value.filter = f
        state = .loaded(value)
    }

    func start(_ id: String) async { await instanceAction(id) { try await $0.startInstance(id) } }
    func stop(_ id: String) async { await instanceAction(id) { try await $0.stopInstance(id) } }

    func delete(_ id: String, force: Bool = false) async {
        await instanceAction(id) { try await $0.deleteInstance(id, force: force) }
    }

    /// Marks `id` busy, runs, refreshes; an `ApiError` lands in
    /// `errorMessage`. Busy always clears.
    private func instanceAction(_ id: String, _ action: (InstanceRepository) async throws -> Void) async {
        if var value = state.value {
            value.busyIds.insert(id)
            state = .loaded(value)
        }
        do {
            try await action(repository())
            await refreshNow()
        } catch let error as ApiError {
            if var value = state.value {
                value.busyIds.remove(id)
                value.errorMessage = error.message
                state = .loaded(value)
            }
        } catch {
            // Dart caught only ApiError; anything else surfaced uncaught.
        }
        if var value = state.value, value.busyIds.contains(id) {
            value.busyIds.remove(id)
            state = .loaded(value)
        }
    }

    func isBusy(_ id: String) -> Bool { state.value?.busyIds.contains(id) ?? false }

    func removeStock(_ instanceId: String, _ symbol: String) async throws {
        try await repository().removeStock(instanceId, symbol)
        await refreshNow()
    }

    func addStock(_ instanceId: String, _ symbol: String) async throws {
        try await repository().addStock(instanceId, symbol)
        await refreshNow()
    }

    func createInstance(
        id: String,
        name: String? = nil,
        granularity: String? = nil,
        runCommand: Bool = false,
        brokerageId: String? = nil,
        maxUsage: Double? = nil,
        strategyId: String? = nil
    ) async throws {
        _ = try await repository().createInstance(
            id: id, name: name, granularity: granularity, runCommand: runCommand,
            brokerageId: brokerageId, maxUsage: maxUsage, strategyId: strategyId
        )
        await refreshNow()
    }

    func createBacktest(
        instanceId: String,
        stocks: [String],
        startDate: String,
        endDate: String,
        granularity: String = "60",
        initialCash: Double = 100_000
    ) async throws {
        try await repository().createBacktest(
            instanceId: instanceId, stocks: stocks, startDate: startDate, endDate: endDate,
            granularity: granularity, initialCash: initialCash
        )
    }

    func linkStrategy(_ instanceId: String, _ strategyId: String) async throws {
        try await repository().linkStrategy(instanceId, strategyId)
        await refreshNow()
    }

    func linkBrokerage(_ instanceId: String, _ brokerageId: String) async throws {
        try await repository().linkBrokerage(instanceId, brokerageId)
        await refreshNow()
    }
}

/// The create sheet's granularity choices (`_kGranularities` / `_kGrans`).
nonisolated let instanceGranularities: [(label: String, value: String)] = [
    ("1 min", "60"),
    ("5 min", "300"),
    ("15 min", "900"),
    ("1 hr", "3600"),
    ("1 day", "86400"),
]

/// `'${b['account_name'] ?? ''} (${b['brokerage_type'] ?? ''})'.trim()`.
nonisolated func instanceBrokerageLabel(_ b: JSONObject) -> String {
    "\((b["account_name"] ?? .null).string ?? "") (\((b["brokerage_type"] ?? .null).string ?? ""))"
        .trimmingCharacters(in: .whitespacesAndNewlines)
}

/// `s['name']?.toString() ?? s['id'].toString()`.
nonisolated func instanceStrategyLabel(_ s: JSONObject) -> String {
    (s["name"] ?? .null).string ?? (s["id"] ?? .null).dartDescription
}

/// `b['id'].toString()`.
nonisolated func instanceSelectorId(_ m: JSONObject) -> String {
    (m["id"] ?? .null).dartDescription
}

// MARK: - Pinned instances

/// Locally persisted pinned instance ids — `PinnedInstancesController`.
/// Pinned instances sort to the top; stored on-device as a JSON array
/// string under `pinned_instances`.
@Observable
final class PinnedInstancesModel {
    static let storageKey = "pinned_instances"

    /// The pinned ids in insertion order (a Dart `LinkedHashSet`).
    private(set) var ordered: [String] = []

    @ObservationIgnored private let store: any SecureStorage

    init(store: any SecureStorage = KeychainStore()) {
        self.store = store
        // Best-effort: pins are a convenience.
        if let raw = store.read(Self.storageKey), !raw.isEmpty,
           let decoded = try? JSON(data: Data(raw.utf8)), let list = decoded.array {
            var seen = Set<String>()
            ordered = list.map(\.dartDescription).filter { seen.insert($0).inserted }
        }
    }

    var pinned: Set<String> { Set(ordered) }

    func isPinned(_ id: String) -> Bool { ordered.contains(id) }

    func toggle(_ id: String) {
        if let i = ordered.firstIndex(of: id) {
            ordered.remove(at: i)
        } else {
            ordered.append(id)
        }
        if let encoded = try? JSON.array(ordered.map(JSON.string)).dartEncoded() {
            try? store.write(Self.storageKey, encoded)
        }
    }
}

/// `instances` with pinned ones first, each group's order kept; unchanged
/// when nothing is pinned.
nonisolated func sortPinnedFirst(_ items: [Instance], _ pinned: Set<String>) -> [Instance] {
    if pinned.isEmpty { return items }
    var pinnedItems: [Instance] = []
    var rest: [Instance] = []
    for i in items {
        if pinned.contains(i.id) { pinnedItems.append(i) } else { rest.append(i) }
    }
    return pinnedItems + rest
}
