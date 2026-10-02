import Foundation

/// Data layer for onboarding endpoints, ported from
/// features/onboarding/data/onboarding_repository.dart. All three return the
/// raw decoded map; `complete` and `reset` both return `{user: {...}}`, which
/// the controller uses to update the session.
nonisolated struct OnboardingRepository: Sendable {
    let client: ApiClient

    /// GET /onboarding/state → `{has_completed_onboarding, counts, user}`.
    func state() async throws -> JSONObject {
        try await client.get("/onboarding/state").orderedObjectValue
    }

    /// POST /onboarding/complete → `{user}`.
    func complete() async throws -> JSONObject {
        try await client.post("/onboarding/complete").orderedObjectValue
    }

    /// POST /onboarding/reset → `{user}`.
    func reset() async throws -> JSONObject {
        try await client.post("/onboarding/reset").orderedObjectValue
    }
}
