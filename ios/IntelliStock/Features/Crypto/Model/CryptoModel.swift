import Foundation
import Observation

/// The crypto instance list — `cryptoInstancesProvider` plus the per-card
/// busy flags of `_CryptoCardState` in crypto_screen.dart.
@Observable
final class CryptoModel {
    private(set) var instances: Loadable<[Instance]> = .loading
    /// Cards with an action in flight.
    private(set) var busy: Set<String> = []

    @ObservationIgnored private let repository: () -> CryptoRepository

    init(repository: @escaping () -> CryptoRepository) {
        self.repository = repository
    }

    /// `ref.invalidate(cryptoInstancesProvider)`: the list stays visible while
    /// it refetches.
    func load() async {
        let repo = repository()
        let result = await Loadable.capture { try await repo.listInstances() }
        if !result.marketsCancelled { instances = result }
    }

    func isBusy(_ id: String) -> Bool { busy.contains(id) }

    /// `_run`: the list is always refetched afterwards (it reflects the
    /// truth). Returns the error text for a toast, nil on success or when
    /// cancelled; Dart swallowed it, which left the screen's error path dead.
    @discardableResult
    func run(_ id: String, _ action: (CryptoRepository) async throws -> Void) async -> String? {
        busy.insert(id)
        var failure: String?
        do {
            try await action(repository())
        } catch {
            if !error.isCancellation { failure = KalshiFormat.errorText(error) }
        }
        busy.remove(id)
        await load()
        return failure
    }

    @discardableResult
    func start(_ id: String) async -> String? { await run(id) { try await $0.startInstance(id) } }
    @discardableResult
    func stop(_ id: String) async -> String? { await run(id) { try await $0.stopInstance(id) } }
    @discardableResult
    func delete(_ id: String) async -> String? { await run(id) { try await $0.deleteInstance(id, force: true) } }

    /// `inst.name.isNotEmpty ? inst.name : inst.id`.
    static func displayName(_ inst: Instance) -> String {
        inst.name.isEmpty ? inst.id : inst.name
    }

    /// The status pill: Crashed / Running / Stopped.
    static func statusLabel(_ inst: Instance) -> String {
        inst.crashed ? "Crashed" : (inst.runCommand ? "Running" : "Stopped")
    }
}
