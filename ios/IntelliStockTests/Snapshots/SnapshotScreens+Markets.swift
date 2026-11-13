import SwiftUI
import Testing
import UIKit
@testable import IntelliStock

/// The Kalshi, Crypto, Brokerages, Models, Token usage and Settings screens
/// for the snapshot harness (see SnapshotHarness.swift for how to run it).
/// Every name starts `markets-`, so `IS_SNAPSHOT_ONLY=markets-` renders just
/// these. Each uses `marketsServices()`: the shared stub plus the markets
/// overrides (SnapshotFixtures+Markets.swift).
@Suite(.serialized, .enabled(if: SnapshotConfig.isEnabled))
@MainActor
struct SnapshotScreensMarkets {
    /// The shared stub with the markets screens' richer routes on top.
    static func marketsServices() -> (services: AppServices, stub: DataStub) {
        let env = Snapshot.makeServices()
        env.stub.handler = { SnapshotFixtures.marketsOverride($0) ?? SnapshotFixtures.respond($0) }
        return env
    }

    // MARK: Kalshi

    @Test func kalshi() async throws {
        try await Snapshot.capture("markets-kalshi", environment: Self.marketsServices(), pages: 4, settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .kalshi)
        }
    }

    @Test func kalshiInstance() async throws {
        try await Snapshot.capture("markets-kalshi-instance", environment: Self.marketsServices(), pages: 8, settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .kalshi, stack: [.kalshiInstance(SnapshotFixtures.kalshiInstanceId)])
        }
    }

    @Test func kalshiInstanceSheet() async throws {
        let env = Self.marketsServices()
        let accounts = try await env.services.dashboardRepository.brokerages().filter { $0.brokerageType == "kalshi" }
        try await Snapshot.capture("markets-kalshi-instance-sheet", environment: env, pages: 4) { services in
            SnapshotHost.sheet {
                KalshiInstanceSheet(
                    accounts: accounts,
                    initialBrokerageId: SnapshotFixtures.kalshiId,
                    repository: { [services] in services.kalshiRepository },
                    onCreated: { _ in }
                )
                .environment(services)
            }
        }
    }

    @Test func kalshiBacktest() async throws {
        try await Snapshot.capture("markets-kalshi-backtest", environment: Self.marketsServices(), pages: 4, settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .kalshi, stack: [.kalshiBacktest(SnapshotFixtures.kalshiInstanceId)])
        }
    }

    @Test func kalshiBacktestResult() async throws {
        try await Snapshot.capture("markets-kalshi-backtest-result", environment: Self.marketsServices(), pages: 4, settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .kalshi, stack: [.kalshiBacktestResult(SnapshotFixtures.kalshiBacktestId)])
        }
    }

    // MARK: Crypto

    @Test func crypto() async throws {
        try await Snapshot.capture("markets-crypto", environment: Self.marketsServices()) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.crypto])
        }
    }

    @Test func cryptoInstance() async throws {
        try await Snapshot.capture("markets-crypto-instance", environment: Self.marketsServices(), pages: 4, settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.crypto, .cryptoInstance(SnapshotFixtures.cryptoInstanceId)])
        }
    }

    @Test func cryptoInstanceSheet() async throws {
        let env = Self.marketsServices()
        let inst = try await env.services.cryptoRepository.getInstance(SnapshotFixtures.cryptoInstanceId)
        try await Snapshot.capture("markets-crypto-instance-sheet", environment: env, pages: 4) { services in
            SnapshotHost.sheet {
                CryptoInstanceSheet(
                    request: CryptoInstanceSheetRequest(edit: inst),
                    repository: { [services] in services.cryptoRepository },
                    onSaved: {}
                )
                .environment(services)
            }
        }
    }

    @Test func cryptoBacktestSheet() async throws {
        let env = Self.marketsServices()
        let inst = try await env.services.cryptoRepository.getInstance(SnapshotFixtures.cryptoInstanceId)
        try await Snapshot.capture("markets-crypto-backtest-sheet", environment: env) { services in
            SnapshotHost.sheet {
                CryptoBacktestSheet(inst: inst, repository: { [services] in services.cryptoRepository }, onCreated: {})
                    .environment(services)
            }
        }
    }

    // MARK: Brokerages

    @Test func brokerages() async throws {
        try await Snapshot.capture("markets-brokerages", environment: Self.marketsServices()) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.brokerages])
        }
    }

    @Test func brokerageSheet() async throws {
        try await Snapshot.capture("markets-brokerage-sheet", environment: Self.marketsServices()) { services in
            SnapshotHost.sheet {
                LinkBrokerageSheet(editAccount: nil, onSaved: {})
                    .environment(services)
            }
        }
    }

    @Test func brokerageSheetEdit() async throws {
        let env = Self.marketsServices()
        let accounts = try await env.services.brokerageRepository.list()
        let expired = try #require(accounts.first { $0.id == SnapshotFixtures.swingPaperId })
        try await Snapshot.capture("markets-brokerage-sheet-edit", environment: env) { services in
            SnapshotHost.sheet {
                LinkBrokerageSheet(editAccount: expired, onSaved: {})
                    .environment(services)
            }
        }
    }

    // MARK: Models

    @Test func models() async throws {
        try await Snapshot.capture("markets-models", environment: Self.marketsServices()) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.models])
        }
    }

    @Test func modelEditor() async throws {
        let env = Self.marketsServices()
        let saved = try await env.services.modelRepository.list()
        let model = try #require(saved.first { $0.id == "m-openrouter" })
        try await Snapshot.capture("markets-model-editor", environment: env, pages: 3) { services in
            SnapshotHost.sheet {
                ModelEditorSheet(existing: model, onSaved: {})
                    .environment(services)
            }
        }
    }

    // MARK: Token usage

    @Test func tokenUsage() async throws {
        try await Snapshot.capture("markets-token-usage", environment: Self.marketsServices(), pages: 5, settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.tokenUsage])
        }
    }

    // MARK: Settings

    @Test func settings() async throws {
        try await Snapshot.capture("markets-settings", environment: Self.marketsServices(), pages: 2) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.settings])
        }
    }

    @Test func notifications() async throws {
        try await Snapshot.capture("markets-notifications", environment: Self.marketsServices(), pages: 2) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.settings, .notificationSettings])
        }
    }
}
