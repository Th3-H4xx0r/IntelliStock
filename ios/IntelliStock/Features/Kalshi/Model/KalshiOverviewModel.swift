import Foundation
import Observation

/// View state for the Kalshi tab (`_KalshiScreenState` plus the
/// `kalshi*Provider(bid)` families in kalshi_repository.dart).
///
/// Each brokerage keeps its own instances / portfolio / edges / positions,
/// so switching accounts back shows the last data at once (the families'
/// `keepAlive` after a successful fetch). A refresh keeps the old value on
/// screen while it runs.
@Observable
final class KalshiOverviewModel {
    /// The chosen account; nil until the accounts load (`_selectedId`).
    private(set) var selectedId: String?

    private(set) var instances: [String: Loadable<[KalshiInstance]>] = [:]
    private(set) var portfolio: [String: Loadable<KalshiPortfolio>] = [:]
    private(set) var edges: [String: Loadable<[KalshiEdge]>] = [:]
    private(set) var positions: [String: Loadable<[KalshiPosition]>] = [:]

    @ObservationIgnored private let repository: () -> KalshiRepository

    init(repository: @escaping () -> KalshiRepository) {
        self.repository = repository
    }

    /// `brokeragesProvider.value` filtered to Kalshi accounts.
    static func kalshiAccounts(_ all: [BrokerageAccount]?) -> [BrokerageAccount] {
        (all ?? []).filter { $0.brokerageType == "kalshi" }
    }

    /// The build-time selection rule: drop a stale selection, then default to
    /// the first account. Returns the brokerage that needs a first load, if
    /// any.
    @discardableResult
    func reconcile(accounts: [BrokerageAccount]) -> String? {
        if let id = selectedId, !accounts.contains(where: { $0.id == id }) {
            selectedId = nil
        }
        if selectedId == nil { selectedId = accounts.first?.id }
        guard let id = selectedId, instances[id] == nil else { return nil }
        return id
    }

    func select(_ id: String) {
        selectedId = id
    }

    /// Whether the brokerage's instances have never loaded (Dart's
    /// `instancesAsync.isLoading` before the first value).
    func isLoadingInstances(_ bid: String) -> Bool {
        instances[bid] == nil || (instances[bid]?.isLoading ?? false)
    }

    /// `instancesAsync?.value ?? const []`.
    func instanceList(_ bid: String) -> [KalshiInstance] {
        instances[bid]?.value ?? []
    }

    // MARK: Loads (the family providers)

    /// First load of every card for `bid`, skipping what is already cached.
    func loadIfNeeded(_ bid: String) async {
        async let a: Void = instances[bid] == nil ? loadInstances(bid) : ()
        async let b: Void = portfolio[bid] == nil ? loadPortfolio(bid) : ()
        async let c: Void = edges[bid] == nil ? loadEdges(bid) : ()
        async let d: Void = positions[bid] == nil ? loadPositions(bid) : ()
        _ = await (a, b, c, d)
    }

    /// `_refresh`: invalidate all four families for the selected brokerage.
    func refresh() async {
        guard let bid = selectedId else { return }
        async let a: Void = loadInstances(bid)
        async let b: Void = loadPortfolio(bid)
        async let c: Void = loadEdges(bid)
        async let d: Void = loadPositions(bid)
        _ = await (a, b, c, d)
    }

    func loadInstances(_ bid: String) async {
        let previous = instances[bid]
        if previous?.value == nil { instances[bid] = .loading }
        let repo = repository()
        let result = await Loadable.capture { try await repo.instances(bid) }
        guard !result.marketsCancelled else { instances[bid] = previous; return }
        instances[bid] = keep(result, over: previous)
    }

    func loadPortfolio(_ bid: String) async {
        let previous = portfolio[bid]
        if previous?.value == nil { portfolio[bid] = .loading }
        let repo = repository()
        let result = await Loadable.capture { try await repo.portfolio(bid) }
        guard !result.marketsCancelled else { portfolio[bid] = previous; return }
        portfolio[bid] = result
    }

    func loadEdges(_ bid: String) async {
        let previous = edges[bid]
        if previous?.value == nil { edges[bid] = .loading }
        let repo = repository()
        let result = await Loadable.capture { try await repo.edges(bid) }
        guard !result.marketsCancelled else { edges[bid] = previous; return }
        edges[bid] = result
    }

    func loadPositions(_ bid: String) async {
        let previous = positions[bid]
        if previous?.value == nil { positions[bid] = .loading }
        let repo = repository()
        let result = await Loadable.capture { try await repo.positions(bid) }
        guard !result.marketsCancelled else { positions[bid] = previous; return }
        positions[bid] = keep(result, over: previous)
    }

    /// The instance and position families `keepAlive` only after a non-empty
    /// success: an empty refetch result still replaces the list (it is real
    /// data), but a failed refetch keeps showing the last good list instead
    /// of collapsing the section.
    private func keep<T>(_ result: Loadable<[T]>, over old: Loadable<[T]>?) -> Loadable<[T]> {
        if case .failed = result, let previous = old?.value, !previous.isEmpty {
            return .loaded(previous)
        }
        return result
    }
}
