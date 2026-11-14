import SwiftUI
import Testing
import UIKit
@testable import IntelliStock

/// AI analysis for the snapshot harness (see SnapshotHarness.swift for how to
/// run it). Every name starts `analysis-`, so `IS_SNAPSHOT_ONLY=analysis-`
/// renders just these. Settings › AI analysis shows in `markets-settings`.
@Suite(.serialized, .enabled(if: SnapshotConfig.isEnabled))
@MainActor
struct SnapshotScreensAnalysis {
    static func services(modelConfigured: Bool = true, pending: Bool = false) -> (services: AppServices, stub: DataStub) {
        let env = Snapshot.makeServices()
        env.stub.handler = {
            SnapshotFixtures.analysisOverride($0, modelConfigured: modelConfigured, pending: pending) ?? SnapshotFixtures.respond($0)
        }
        return env
    }

    /// From More: the first holding (GLD) picked, the chart, the moves and
    /// today's account.
    @Test func analysis() async throws {
        try await Snapshot.capture("analysis", environment: Self.services(), pages: 4, settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.analysis(nil)])
        }
    }

    /// From a stock page, on NVDA, with no model chosen: the notice.
    @Test func analysisNoModel() async throws {
        try await Snapshot.capture("analysis-no-model", environment: Self.services(modelConfigured: false), pages: 2, settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.analysis("NVDA")])
        }
    }

    /// Explanations running: the header's progress and a pending row.
    @Test func analysisExplaining() async throws {
        try await Snapshot.capture("analysis-explaining", environment: Self.services(pending: true), pages: 2, settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.analysis("NVDA")])
        }
    }

    /// An unknown ticker.
    @Test func analysisNotFound() async throws {
        try await Snapshot.capture("analysis-not-found", environment: Self.services(), settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.analysis("ZZZZ")])
        }
    }

    /// The earnings day opened: headline, explanation, split, sources.
    @Test func analysisEvent() async throws {
        let env = Self.services()
        let repo = env.services.analysisRepository
        let model = AnalysisModel(symbol: "NVDA", repository: { repo })
        await model.load()
        let event = try #require(model.analysis?.events.first { $0.isExplained && !$0.sources.isEmpty })
        try await Snapshot.capture("analysis-event", environment: env, pages: 2) { _ in
            SnapshotHost.sheet {
                AnalysisEventSheet(model: model, eventId: event.id, onOpenSettings: {})
            }
        }
    }

    /// An unexplained day opened: Explain.
    @Test func analysisEventUnexplained() async throws {
        let env = Self.services()
        let repo = env.services.analysisRepository
        let model = AnalysisModel(symbol: "NVDA", repository: { repo })
        await model.load()
        let event = try #require(model.analysis?.events.first { $0.status == .unexplained })
        try await Snapshot.capture("analysis-event-unexplained", environment: env) { _ in
            SnapshotHost.sheet(detents: [.medium]) {
                AnalysisEventSheet(model: model, eventId: event.id, onOpenSettings: {})
            }
        }
    }

    /// Settings with the AI analysis section.
    @Test func analysisSettings() async throws {
        try await Snapshot.capture("analysis-settings", environment: Self.services(), pages: 2) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.settings])
        }
    }
}
