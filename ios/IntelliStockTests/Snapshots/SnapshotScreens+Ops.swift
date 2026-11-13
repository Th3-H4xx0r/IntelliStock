import SwiftUI
import Testing
import UIKit
@testable import IntelliStock

/// The Ops screens (Instances, Backtests, Strategies, Agent runs, Learning,
/// Nexus) for the snapshot harness (see SnapshotHarness.swift). Skipped
/// unless `IS_SNAPSHOT_DIR` is set. Serialized: one window on screen at a
/// time. Names start `ops-` so `IS_SNAPSHOT_ONLY=ops-` renders them all.
///
/// Backtests and Strategies live in SnapshotScreens+OpsBacktests.swift and
/// Agent runs, Learning and Nexus in SnapshotScreens+OpsResearch.swift, as
/// extensions of this suite.
@Suite(.serialized, .enabled(if: SnapshotConfig.isEnabled))
@MainActor
struct SnapshotScreensOps {
    // MARK: Instances

    /// The list with three pinned cards (running, crashed, stopped) over the
    /// other instances.
    @Test func instancesList() async throws {
        try await Self.withPins {
            try await Snapshot.capture("ops-instances", pages: 3, settle: .seconds(6)) { services in
                SnapshotHost.shell(services, tab: .instances)
            }
        }
    }

    /// The list with nothing pinned: every instance as a row.
    @Test func instancesUnpinned() async throws {
        try await Self.withPins([]) {
            try await Snapshot.capture("ops-instances-rows", settle: .seconds(5)) { services in
                SnapshotHost.shell(services, tab: .instances)
            }
        }
    }

    /// The real-money instance's detail, scrolled through.
    @Test func instanceDetail() async throws {
        try await Snapshot.capture("ops-instance-detail", pages: 4, settle: .seconds(5)) { services in
            SnapshotHost.shell(services, tab: .instances, stack: [.instance("alpaca-main")])
        }
    }

    /// A stopped instance with no brokerage or stocks.
    @Test func instanceDetailStopped() async throws {
        try await Snapshot.capture("ops-instance-detail-stopped", pages: 2, settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .instances, stack: [.instance("nexus-lab")])
        }
    }

    @Test func instanceCreateSheet() async throws {
        try await Snapshot.capture("ops-instance-create", settle: .seconds(3)) { services in
            SnapshotHost.sheet { InstanceCreateSheet(model: services.instances).environment(services) }
        }
    }

    @Test func instanceAddStockSheet() async throws {
        try await Snapshot.capture("ops-instance-add-stock") { _ in
            SnapshotHost.sheet { InstanceAddStockSheet { _ in } }
        }
    }

    @Test func instanceLinkSheet() async throws {
        try await Snapshot.capture("ops-instance-link", settle: .seconds(3)) { services in
            SnapshotHost.sheet {
                InstanceLinkSheet(kind: .brokerage, currentId: SnapshotFixtures.alpacaLiveId) { _ in }
                    .environment(services)
            }
        }
    }

    @Test func instanceCreateBacktestSheet() async throws {
        try await Snapshot.capture("ops-instance-new-backtest") { services in
            SnapshotHost.sheet {
                InstanceCreateBacktestSheet(model: Self.detailModel(services, "alpaca-main"))
            }
        }
    }

    @Test func instanceClearStateSheet() async throws {
        try await Snapshot.capture("ops-instance-clear") { services in
            SnapshotHost.sheet {
                InstanceClearStateSheet(instanceId: "alpaca-main", model: Self.detailModel(services, "alpaca-main"))
            }
        }
    }

    // MARK: Helpers

    static func detailModel(_ services: AppServices, _ id: String) -> InstanceDetailModel {
        InstanceDetailModel(instanceId: id, repository: { [unowned services] in services.instanceRepository })
    }

    /// Runs `body` with `ids` pinned (the device's pin store), then puts the
    /// store back as it was.
    static func withPins(_ ids: [String] = SnapshotFixtures.opsPinnedInstanceIds, _ body: () async throws -> Void) async throws {
        let store = KeychainStore()
        let key = PinnedInstancesModel.storageKey
        let previous = store.read(key)
        let encoded = "[" + ids.map { "\"\($0)\"" }.joined(separator: ",") + "]"
        try store.write(key, encoded)
        defer {
            if let previous { try? store.write(key, previous) } else { store.delete(key) }
        }
        try await body()
    }
}
