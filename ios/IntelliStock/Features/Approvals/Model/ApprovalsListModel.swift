import Foundation
import Observation

/// The approvals list as shown: the requests waiting for the operator, and
/// the rest.
nonisolated struct ApprovalsListState: Hashable, Sendable {
    /// Pending and not yet expired, soonest expiry first.
    var waiting: [McpApproval] = []
    /// Everything else, newest first.
    var recent: [McpApproval] = []
    /// The last refresh's failure while the older list stays on screen.
    var refreshError: String?
}

/// Splits rows into waiting (pending and not past `expires_at`, soonest
/// expiry first) and recent (everything else, newest first, undated last).
/// Ties keep the server's order.
nonisolated func splitApprovals(_ rows: [McpApproval], now: Date) -> (waiting: [McpApproval], recent: [McpApproval]) {
    let indexed = Array(rows.enumerated())
    let waiting = indexed
        .filter { $0.element.isPending && !$0.element.isExpired(at: now) }
        .sorted { a, b in
            // A waiting row always has an expiry: isExpired counts a missing one as expired.
            let x = a.element.expiresAt ?? .distantFuture, y = b.element.expiresAt ?? .distantFuture
            return x == y ? a.offset < b.offset : x < y
        }
    let recent = indexed
        .filter { !($0.element.isPending && !$0.element.isExpired(at: now)) }
        .sorted { a, b in
            switch (a.element.createdAt, b.element.createdAt) {
            case let (x?, y?): x == y ? a.offset < b.offset : x > y
            case (.some, nil): true
            case (nil, .some): false
            case (nil, nil): a.offset < b.offset
            }
        }
    return (waiting.map(\.element), recent.map(\.element))
}

/// More › Approvals: Jarvis's recent requests, read as one page
/// (`GET /mcp/approvals?status=all&limit=50`) and polled every 15 s,
/// pausing in the background.
@Observable
final class ApprovalsListModel {
    static let pollEvery: Duration = .seconds(15)

    private(set) var state: Loadable<ApprovalsListState> = .loading

    @ObservationIgnored private let source: () -> any ApprovalsSource
    @ObservationIgnored private let clock: () -> Date

    init(source: @escaping () -> any ApprovalsSource, clock: @escaping () -> Date = Date.init) {
        self.source = source
        self.clock = clock
    }

    /// The first load and Retry: a failure becomes `.failed` (there is no
    /// list to show); a cancelled load changes nothing.
    func load() async {
        state = await state.refreshing { try await fetch() }
    }

    /// Pull to refresh and each poll. A failure keeps the last list and sets
    /// `refreshError` (or `.failed` while there is no list); a cancelled
    /// refresh changes nothing.
    func refresh() async {
        do {
            state = .loaded(try await fetch())
        } catch where error.isCancellation {
            return
        } catch {
            guard var current = state.value else {
                state = .failed(error)
                return
            }
            current.refreshError = approvalsErrorText(error)
            state = .loaded(current)
        }
    }

    /// Loads if nothing is loaded, then refreshes every `pollEvery` until
    /// the calling task is cancelled, pausing in the background. A failed
    /// first load is retried by the next tick.
    func poll(lifecycle: AppLifecycle?, sleep: @escaping PollingSleep = realPollingSleep) async {
        if state.value == nil { await load() }
        guard !Task.isCancelled else { return }
        await PollingLoop(interval: { Self.pollEvery }, sleep: sleep) { [weak self] in
            await self?.refresh()
        }
        .run(lifecycle: lifecycle)
    }

    private func fetch() async throws -> ApprovalsListState {
        let rows = try await source().approvals(status: .all, limit: ApprovalsRepository.listLimit)
        let (waiting, recent) = splitApprovals(rows, now: clock())
        return ApprovalsListState(waiting: waiting, recent: recent)
    }
}
