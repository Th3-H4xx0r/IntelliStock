import Foundation

/// A value that loads asynchronously — Riverpod's `AsyncValue` (`.loading`,
/// `.data`, `.error`).
///
/// Where a Dart screen kept showing data during a refresh, keep the model in
/// `.loaded(old)` while the refresh runs and track the spinner separately.
nonisolated enum Loadable<Value> {
    case loading
    case loaded(Value)
    case failed(any Error)

    var value: Value? {
        if case .loaded(let v) = self { return v }
        return nil
    }

    var error: (any Error)? {
        if case .failed(let e) = self { return e }
        return nil
    }

    var isLoading: Bool {
        if case .loading = self { return true }
        return false
    }

    var hasValue: Bool { value != nil }

    func map<T>(_ transform: (Value) throws -> T) rethrows -> Loadable<T> {
        switch self {
        case .loading: .loading
        case .loaded(let v): .loaded(try transform(v))
        case .failed(let e): .failed(e)
        }
    }

    /// `AsyncValue.guard`: runs `body`, capturing success or failure.
    static func capture(_ body: () async throws -> Value) async -> Loadable<Value> {
        do {
            return .loaded(try await body())
        } catch {
            return .failed(error)
        }
    }
}

extension Loadable: Sendable where Value: Sendable {}

nonisolated extension Loadable {
    /// The message to show for a failure: `ApiError.message`, else the
    /// error's description (Dart's `e.toString()`).
    var errorMessage: String? {
        guard let error else { return nil }
        if let api = error as? ApiError { return api.message }
        return error.localizedDescription
    }
}
