import Foundation

/// Data layer for onboarding endpoints, ported from
/// features/onboarding/data/onboarding_repository.dart. All three return the
/// raw decoded map; `complete` and `reset` both return `{user: {...}}`, which
/// the controller uses to update the session.
nonisolated struct OnboardingRepository: Sendable {
    let client: ApiClient

    /// GET /onboarding/state → `{has_completed_onboarding, counts, user}`.
    func state() async throws -> [String: JSON] {
        try await client.get("/onboarding/state").objectValue
    }

    /// POST /onboarding/complete → `{user}`.
    func complete() async throws -> [String: JSON] {
        try await client.post("/onboarding/complete").objectValue
    }

    /// POST /onboarding/reset → `{user}`.
    func reset() async throws -> [String: JSON] {
        try await client.post("/onboarding/reset").objectValue
    }
}
