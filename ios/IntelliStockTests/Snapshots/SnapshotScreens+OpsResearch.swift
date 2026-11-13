import SwiftUI
import Testing
import UIKit
@testable import IntelliStock

/// Agent runs, Learning and Nexus screens for the snapshot harness, as part
/// of the Ops suite (SnapshotScreens+Ops.swift). Names start `ops-`.
extension SnapshotScreensOps {
    // MARK: Agent runs

    /// The agent stopped (the shared control): Start, and a stale run.
    @Test func agentRuns() async throws {
        try await Snapshot.capture("ops-agent-runs", pages: 5, settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.agentRuns])
        }
    }

    /// The agent running: Pause and Stop.
    @Test func agentRunsRunning() async throws {
        let env = Self.researchServices(agentRunning: true)
        try await Snapshot.capture("ops-agent-runs-running", environment: env, settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.agentRuns])
        }
    }

    @Test func agentStartSheet() async throws {
        try await Snapshot.capture("ops-agent-start") { _ in
            SnapshotHost.sheet(detents: [.medium]) { AgentStartSheet { _ in } }
        }
    }

    @Test func agentResumeSheet() async throws {
        try await Snapshot.capture("ops-agent-resume") { _ in
            SnapshotHost.sheet(detents: [.medium]) { AgentResumeSheet { _ in } }
        }
    }

    // MARK: Learning

    @Test func learning() async throws {
        try await Snapshot.capture("ops-learning", pages: 5, settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.learning])
        }
    }

    /// The first finding opened: its thread and the ladder.
    @Test func learningFindingOpen() async throws {
        let env = Snapshot.makeServices()
        let findings = try await env.services.learningRepository.findings()
        let first = try #require(findings.first)
        try await Snapshot.capture("ops-learning-finding", environment: env) { _ in
            NavigationStack {
                List {
                    DSSection("Findings & reports") {
                        LearningFindingRow(finding: first, open: true)
                    }
                }
                .dsList()
                .navigationTitle("Learning")
                .navigationBarTitleDisplayMode(.inline)
            }
        }
    }

    @Test func learningTargetsSheet() async throws {
        let env = Snapshot.makeServices()
        let repo = env.services.learningRepository
        let model = LearningModel(repository: { repo })
        await model.load()
        let targets = try #require(model.state.value?.targets)
        try await Snapshot.capture("ops-learning-targets", environment: env) { _ in
            SnapshotHost.sheet { LearningTargetsSheet(targets: targets, model: model) }
        }
    }

    // MARK: Nexus

    /// The shared (bare) status: idle, nothing built.
    @Test func nexus() async throws {
        try await Snapshot.capture("ops-nexus", pages: 2, settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.nexus])
        }
    }

    @Test func nexusBuilt() async throws {
        let env = Self.researchServices(nexus: .built)
        try await Snapshot.capture("ops-nexus-built", environment: env, pages: 4, settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.nexus])
        }
    }

    @Test func nexusBuilding() async throws {
        let env = Self.researchServices(nexus: .building)
        try await Snapshot.capture("ops-nexus-building", environment: env, pages: 4, settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.nexus])
        }
    }

    @Test func nexusStartSheet() async throws {
        let (env, model, status) = try await Self.researchNexus(.built)
        try await Snapshot.capture("ops-nexus-start", environment: env, pages: 2) { _ in
            SnapshotHost.sheet { NexusStartSheet(status: status, model: model) }
        }
    }

    @Test func nexusAutoUpdateSheet() async throws {
        let (env, model, status) = try await Self.researchNexus(.built)
        try await Snapshot.capture("ops-nexus-auto-update", environment: env) { _ in
            SnapshotHost.sheet { NexusAutoUpdateSheet(status: status, model: model) }
        }
    }

    @Test func nexusRebuildSheet() async throws {
        let (env, model, _) = try await Self.researchNexus(.built)
        try await Snapshot.capture("ops-nexus-rebuild", environment: env, pages: 2) { _ in
            SnapshotHost.sheet { NexusRebuildSheet(model: model) }
        }
    }

    @Test func nexusDeleteSheet() async throws {
        let (env, model, status) = try await Self.researchNexus(.built)
        try await Snapshot.capture("ops-nexus-delete", environment: env) { _ in
            SnapshotHost.sheet { NexusDeleteSheet(status: status, model: model) }
        }
    }

    // MARK: Helpers

    /// Services over the snapshot fixtures with this area's overrides: a
    /// running agent, or a fuller Nexus status.
    static func researchServices(agentRunning: Bool = false, nexus: SnapshotFixtures.ResearchNexus? = nil) -> (services: AppServices, stub: DataStub) {
        let stub = DataStub()
        stub.handler = { request in
            SnapshotFixtures.researchOverride(request, agentRunning: agentRunning, nexus: nexus) ?? SnapshotFixtures.respond(request)
        }
        return (AppServices(apiClient: stub.client), stub)
    }

    /// A Nexus model loaded with `variant`'s status, for the sheets.
    static func researchNexus(_ variant: SnapshotFixtures.ResearchNexus) async throws -> ((services: AppServices, stub: DataStub), NexusModel, NexusStatus) {
        let env = researchServices(nexus: variant)
        let repo = env.services.nexusRepository
        let model = NexusModel(repository: { repo })
        await model.refreshNow()
        let status = try #require(model.statusValue)
        return (env, model, status)
    }
}
