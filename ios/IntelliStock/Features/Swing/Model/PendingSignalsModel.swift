import Foundation
import Observation

// Ported from features/swing/application/swing_controller.dart.

// MARK: - Which lanes does this instance run?

nonisolated struct SwingLanes: Hashable, Sendable {
    static let none = SwingLanes(swing: false, wheel: false)

    let swing: Bool
    let wheel: Bool

    var any: Bool { swing || wheel }
}

nonisolated private let camelBoundary = try! NSRegularExpression(pattern: "([a-z0-9])([A-Z])")

/// `_canonicalStrategyId`: trim, camelCase → snake_case, lower-case.
nonisolated func swingCanonicalStrategyId(_ raw: JSON) -> String {
    let text = (raw.isNull ? "" : raw.dartDescription).trimmingCharacters(in: .whitespacesAndNewlines)
    let ns = text as NSString
    let snake = camelBoundary.stringByReplacingMatches(
        in: text, range: NSRange(location: 0, length: ns.length), withTemplate: "$1_$2"
    )
    return snake.lowercased()
}

/// Reads the instance's nested strategy document (`Instance.strategy`).
/// Accepts the lowercase id ("strategy_swing") and the class name
/// ("StrategySwing").
nonisolated func swingLanesOf(_ strategyDoc: JSONObject?) -> SwingLanes {
    guard let subs = strategyDoc?["strategies"]?.array else { return .none }
    var swing = false
    var wheel = false
    for sub in subs where sub.isObject {
        let id = swingCanonicalStrategyId(sub["strategy"])
        if id == "strategy_swing" { swing = true }
        if id == "strategy_wheel" { wheel = true }
    }
    return SwingLanes(swing: swing, wheel: wheel)
}

// MARK: - Copy shared by the confirm dialog and the toast

nonisolated func decisionLabel(_ decision: String) -> String {
    switch decision {
    case "approve": "Approve"
    case "approve_half": "Approve ½"
    default: "Reject"
    }
}

// Follow-up 5: an approval is not final. A transient refusal (no quote before
// the open, say) puts the signal back to pending.
nonisolated private let approveAfter = "Approving sends the order. If the broker can't place it "
    + "yet (e.g. before the open) the signal returns here to approve again."

nonisolated func decisionConfirmBody(_ s: SwingSignal, _ decision: String) -> String {
    switch decision {
    case "approve":
        "Approve \(s.symbol)? The broker rebuilds the order at the live price and checks it before sending. \(approveAfter)"
    case "approve_half":
        "Approve \(s.symbol) at half size? The broker rebuilds the order at the live price and checks it before sending. \(approveAfter)"
    default:
        "Reject \(s.symbol)? Decisions are final."
    }
}

nonisolated func decisionSuccessMessage(_ s: SwingSignal, _ decision: String) -> String {
    switch decision {
    // No notification is promised: some refusals send none (FW item 4).
    case "approve":
        "Approved \(s.symbol). The broker rebuilds and checks the order at the live price before sending it."
    case "approve_half":
        "Approved \(s.symbol) at half size. The broker rebuilds and checks the order at the live price before sending it."
    default:
        "Rejected \(s.symbol)."
    }
}

/// FW-api-I1 / follow-up 1: the wording for a 202 (the server sends the same
/// text); the fallback when a 202 carries no detail.
nonisolated func uncertainMessage(_ what: String = "Approval") -> String {
    "\(what) received, but its delivery to the broker could not be confirmed. "
        + "Do NOT place this order by hand — it may still be queued. The card will "
        + "show submitted or failed shortly."
}

nonisolated let kUncertainApproval = uncertainMessage()

// MARK: - Outcomes

nonisolated enum DecisionOutcome: Hashable, Sendable {
    /// The server recorded it; the card goes. It comes back only if the
    /// broker puts the signal back to pending (FW-api-I2).
    case recorded
    /// 202 (FW-api-I1): the approval is recorded but the broker command may
    /// or may not be queued.
    case uncertain
    /// 400/404/409: already decided elsewhere (or gone). The card is removed.
    case noLongerPending
    /// Anything else (401, 403, 5xx, network). The card stays.
    case failed
    /// A second tap while the first request was in flight. Nothing was sent.
    case ignored
}

nonisolated struct DecisionResult: Hashable, Sendable {
    let outcome: DecisionOutcome
    let message: String

    init(_ outcome: DecisionOutcome, _ message: String) {
        self.outcome = outcome
        self.message = message
    }
}

/// An approval no broker command has claimed this long is offered a re-send
/// (fix wave item 3).
nonisolated let stuckAfter: TimeInterval = 120

/// `now.difference(from)` in seconds.
nonisolated private func elapsed(_ from: Date, _ to: Date) -> TimeInterval {
    to.timeIntervalSince(from)
}

/// Approved signals no broker command has claimed for more than
/// `stuckAfter`, counted from the later of the decision and this device's
/// last re-send. An undated one counts as stuck.
nonisolated func stuckApprovals(_ approved: [SwingSignal], _ now: Date, _ resentAt: [String: Date]) -> [SwingSignal] {
    approved.filter { s in
        let times = [s.decidedAt, resentAt[s.id]].compactMap { $0 }
        guard let since = times.max() else { return true }
        return elapsed(since, now) > stuckAfter
    }
}

nonisolated func stuckLabel(_ s: SwingSignal, _ now: Date) -> String {
    guard let at = s.decidedAt else { return "Approved; the broker has not picked it up yet." }
    let mins = Int((elapsed(at, now) / 60).rounded(.towardZero))
    return "Approved \(mins < 1 ? 1 : mins) min ago; the broker has not picked it up yet."
}

/// The New York calendar date ("YYYY-MM-DD") at `instant`: EDT (UTC-4) from
/// 02:00 local on the second Sunday of March to 02:00 local on the first
/// Sunday of November, EST (UTC-5) otherwise.
nonisolated func nyDate(_ instant: Date) -> String {
    var utc = Calendar(identifier: .gregorian)
    utc.timeZone = TimeZone(secondsFromGMT: 0)!
    let year = utc.component(.year, from: instant)
    func nthSunday(_ month: Int, _ n: Int) -> Date {
        let first = utc.date(from: DateComponents(year: year, month: month, day: 1))!
        // Calendar weekday: 1 = Sunday.
        let weekday = utc.component(.weekday, from: first)
        let offset = (8 - weekday) % 7
        return first.addingTimeInterval(Double(offset + 7 * (n - 1)) * 86_400)
    }
    let dstStart = nthSunday(3, 2).addingTimeInterval(7 * 3600) // 02:00 EST
    let dstEnd = nthSunday(11, 1).addingTimeInterval(6 * 3600) // 02:00 EDT
    let edt = !(instant < dstStart) && instant < dstEnd
    let local = instant.addingTimeInterval(-Double(edt ? 4 : 5) * 3600)
    let c = utc.dateComponents([.year, .month, .day], from: local)
    func two(_ v: Int) -> String { v < 10 ? "0\(v)" : "\(v)" }
    return "\(c.year!)-\(two(c.month!))-\(two(c.day!))"
}

/// Round 3 minor 1: the server re-sends only an approval made today in New
/// York, and the button follows the same rule. nil when the card may offer
/// Re-send, else the reason shown in its place. `today` is `nyDate(now)`.
nonisolated func resendBlockedReason(_ s: SwingSignal, _ today: String) -> String? {
    let madeOn = s.decidedAt.map(nyDate)
    if let madeOn, madeOn == today { return nil }
    return "This approval was made on \(madeOn ?? "an unknown date"); approve a fresh signal instead."
}

nonisolated func resendConfirmBody(_ s: SwingSignal) -> String {
    "Re-send the approval for \(s.symbol)? The broker rebuilds the order at the "
        + "live price and checks it before sending; a copy it already picked up is "
        + "ignored."
}

/// Follow-up 2: the badge on a card whose approval or re-send answered 202.
nonisolated let uncertainBadge = "uncertain — waiting for the broker"

/// Round 3 FU-1: what a waiting card says.
nonisolated let waitingCopy = "Delivery to the broker could not be confirmed. Do NOT "
    + "place this order by hand. This should show submitted or failed within a "
    + "minute; if it is still waiting after 2 minutes you can re-send it here."

/// A 202'd approval or re-send, kept on its own card until a poll begun
/// after the 202 settles it (follow-up 2).
nonisolated struct UncertainCard: Hashable, Sendable {
    let signal: SwingSignal
    /// The newest fetch generation started when the 202 arrived.
    let since: Int
    /// When the 202 arrived (device clock).
    let sinceAt: Date
    /// nil while waiting, then "submitted" or "failed".
    let resolved: String?

    init(signal: SwingSignal, since: Int, sinceAt: Date, resolved: String? = nil) {
        self.signal = signal
        self.since = since
        self.sinceAt = sinceAt
        self.resolved = resolved
    }

    var badge: String { resolved ?? uncertainBadge }

    /// Never without an action: Dismiss once settled, or once `stuckAfter`
    /// has passed since the 202.
    func canDismiss(_ now: Date) -> Bool {
        resolved != nil || elapsed(sinceAt, now) > stuckAfter
    }

    func settled(_ status: String) -> UncertainCard {
        UncertainCard(signal: signal, since: since, sinceAt: sinceAt, resolved: status)
    }
}

nonisolated struct PendingSignalsState: Hashable, Sendable {
    var signals: [SwingSignal] = []
    /// Approved signals no broker command has claimed for `stuckAfter`.
    var stuck: [SwingSignal] = []
    /// 202'd approvals and re-sends, waiting for the broker.
    var uncertain: [UncertainCard] = []
    /// Signal ids whose decision request is in flight.
    var deciding: Set<String> = []
    /// Signal ids whose re-send request is in flight.
    var resending: Set<String> = []
    /// Set when the latest poll failed; `signals` is then the last good list.
    var refreshError: String?
    /// The clock `stuck` was computed at.
    var asOf: Date?

    func isDeciding(_ id: String) -> Bool { deciding.contains(id) }
    func isResending(_ id: String) -> Bool { resending.contains(id) }
}

// MARK: - The repository seam

/// The swing endpoints `PendingSignalsModel` and `WheelModel` read — the
/// shape Dart's `FakeSwingRepo implements SwingRepository` stood in for.
nonisolated protocol SwingSignalsSource: Sendable {
    func pendingSignals(_ instanceId: String) async throws -> [SwingSignal]
    func approvedSignals(_ instanceId: String) async throws -> [SwingSignal]
    func signalsWithStatus(_ instanceId: String, _ status: String) async throws -> [SwingSignal]
    func decide(_ instanceId: String, _ signalId: String, _ decision: String, reason: String?) async throws -> DecisionReceipt
    func resend(_ instanceId: String, _ signalId: String) async throws -> DecisionReceipt
    func wheel(_ instanceId: String) async throws -> WheelSnapshot
}

extension SwingRepository: SwingSignalsSource {}

/// One poll's reads, each settled on its own (follow-up 4).
private struct SwingLoad {
    let pending: [SwingSignal]?
    let approved: [SwingSignal]?
    let error: (any Error)?
    var submitted: [SwingSignal]?
    var failed: [SwingSignal]?
}

/// The `toString()` of a Dart error: an `ApiError` prints its message.
nonisolated func swingErrorText(_ error: any Error) -> String {
    if let api = error as? ApiError { return api.message }
    return "\(error)"
}

// MARK: - Pending signals (polled)

/// AI-scored candidates awaiting a decision, polled every 30 s —
/// `PendingSignalsNotifier`. Approving or rejecting is a REAL trading action:
/// the guards (double-tap, hides, generations, the 202 waiting cards and the
/// stuck re-send rules) are ported exactly.
@Observable
final class PendingSignalsModel {
    static let pollEvery: Duration = .seconds(30)

    let instanceId: String
    private(set) var state: Loadable<PendingSignalsState> = .loading

    @ObservationIgnored private let source: () -> any SwingSignalsSource
    @ObservationIgnored private let clock: () -> Date

    /// FW-api-I2: id -> the newest generation started when its 2xx arrived.
    @ObservationIgnored private var hidden: [String: Int] = [:]
    @ObservationIgnored private var generation = 0
    /// The newest generation whose answer is on screen.
    @ObservationIgnored private var applied = 0
    /// id -> when this device last re-sent it (fix wave item 3).
    @ObservationIgnored private var resentAt: [String: Date] = [:]
    /// id -> the waiting card after a 202 (follow-up 2), in insertion order.
    @ObservationIgnored private var uncertainCards: [(id: String, card: UncertainCard)] = []
    /// Stuck ids the operator dismissed on this device (round 3 FU-1).
    @ObservationIgnored private var dismissedStuck: Set<String> = []
    /// Ids that left the waiting state for the stuck list (round 4, R3-1).
    @ObservationIgnored private var joinedStuck: Set<String> = []

    init(instanceId: String, source: @escaping () -> any SwingSignalsSource, clock: @escaping () -> Date = Date.init) {
        self.instanceId = instanceId
        self.source = source
        self.clock = clock
    }

    var value: PendingSignalsState? { state.value }

    // MARK: Uncertain-card map helpers (Dart's insertion-ordered map)

    private func uncertainCard(_ id: String) -> UncertainCard? {
        uncertainCards.first { $0.id == id }?.card
    }

    private func setUncertain(_ id: String, _ card: UncertainCard) {
        if let i = uncertainCards.firstIndex(where: { $0.id == id }) {
            uncertainCards[i].card = card
        } else {
            uncertainCards.append((id, card))
        }
    }

    private func removeUncertain(_ id: String) {
        uncertainCards.removeAll { $0.id == id }
    }

    private var uncertainValues: [UncertainCard] { uncertainCards.map(\.card) }

    // MARK: Build / refresh

    /// The first load, and every pull-to-refresh / Retry (`ref.invalidate`
    /// rebuilds the same notifier, so only the hides reset).
    func build() async {
        hidden.removeAll()
        generation += 1
        let gen = generation
        let load = await fetch()
        guard load.pending != nil else {
            // No pending list at all: the section shows the error with Retry.
            if let error = load.error, error.isCancellationOrTaskCancelled { return }
            state = .failed(load.error ?? ApiError(message: "Could not load signals."))
            return
        }
        if gen > applied { applied = gen }
        state = .loaded(apply(PendingSignalsState(), gen, load))
    }

    /// First load, then a poll every 30 s, pausing in the background.
    ///
    /// The loop always runs (Dart restarted its poller on every successful
    /// build): while there is no list yet, a failed first load included, a
    /// tick builds instead of refreshing, so the section recovers by itself.
    func poll(lifecycle: AppLifecycle?, sleep: @escaping PollingSleep = realPollingSleep) async {
        if state.value == nil { await build() }
        // Left during the first fetch: no poller outlives the screen.
        guard !Task.isCancelled else { return }
        await PollingLoop(interval: { Self.pollEvery }, sleep: sleep) { [weak self] in
            guard let self else { return }
            if self.state.value == nil {
                await self.build()
            } else {
                await self.refresh()
            }
        }
        .run(lifecycle: lifecycle)
    }

    /// The pending list and the approved lists, requested together and
    /// settled independently (follow-up 4).
    private func fetch() async -> SwingLoad {
        let repo = source()
        let id = instanceId
        let waiting = uncertainCards.contains { $0.card.resolved == nil }
        async let pendingRead = Self.settle { try await repo.pendingSignals(id) }
        async let approvedRead = Self.settle { try await repo.approvedSignals(id) }
        async let submittedRead = waiting ? Self.settle { try await repo.signalsWithStatus(id, "submitted") } : (nil, nil)
        async let failedRead = waiting ? Self.settle { try await repo.signalsWithStatus(id, "failed") } : (nil, nil)
        let (pending, pendingError) = await pendingRead
        let (approved, approvedError) = await approvedRead
        let (submitted, submittedError) = await submittedRead
        let (failed, failedError) = await failedRead
        return SwingLoad(
            pending: pending,
            approved: approved,
            error: pendingError ?? approvedError ?? submittedError ?? failedError,
            submitted: submitted,
            failed: failed
        )
    }

    nonisolated private static func settle(_ read: () async throws -> [SwingSignal]) async -> ([SwingSignal]?, (any Error)?) {
        do {
            return (try await read(), nil)
        } catch {
            return (nil, error)
        }
    }

    /// Follow-up 2: settle the waiting cards a fetch begun after their 202
    /// can speak for. Returns the ids that joined the stuck list.
    private func foldUncertain(_ gen: Int, _ load: SwingLoad, _ now: Date) -> Set<String> {
        func ids(_ rows: [SwingSignal]?) -> Set<String>? { rows.map { Set($0.map(\.id)) } }
        let pending = ids(load.pending)
        let submitted = ids(load.submitted?.filter { !($0.orderClientId ?? "").isEmpty })
        let failed = ids(load.failed)
        let approved = ids(load.approved) ?? []
        var joined: Set<String> = []
        for (id, card) in uncertainCards {
            if card.resolved != nil || gen <= card.since { continue }
            if pending?.contains(id) ?? false {
                removeUncertain(id)
            } else if submitted?.contains(id) ?? false {
                setUncertain(id, card.settled("submitted"))
            } else if failed?.contains(id) ?? false {
                setUncertain(id, card.settled("failed"))
            } else if approved.contains(id), elapsed(card.sinceAt, now) > stuckAfter {
                removeUncertain(id)
                joined.insert(id)
            }
        }
        return joined
    }

    /// A failed read keeps its part of `current`; the first error is shown.
    private func apply(_ current: PendingSignalsState, _ gen: Int, _ load: SwingLoad) -> PendingSignalsState {
        let now = clock()
        let pending = load.pending
        let approved = load.approved
        joinedStuck.formUnion(foldUncertain(gen, load, now))
        if let approved {
            let ids = Set(approved.map(\.id))
            joinedStuck = joinedStuck.filter { ids.contains($0) }
        }
        func snoozed(_ id: String) -> Bool {
            guard let at = resentAt[id] else { return false }
            return elapsed(at, now) <= stuckAfter
        }
        let base = approved.map { stuckApprovals($0, now, resentAt) } ?? current.stuck
        let listed = Set(base.map(\.id))
        let joinedRows = (approved ?? []).filter {
            joinedStuck.contains($0.id) && !listed.contains($0.id) && !snoozed($0.id)
        }
        var next = current
        if let pending {
            next.signals = visible(gen, pending, nonPending: (approved ?? []).map(\.id))
        }
        next.stuck = (base + joinedRows).filter {
            uncertainCard($0.id) == nil && !dismissedStuck.contains($0.id)
        }
        next.uncertain = uncertainValues
        next.asOf = now
        next.refreshError = load.error.map(swingErrorText)
        return next
    }

    /// The fetch's pending rows minus hidden ids and ids the same fetch saw
    /// with another status. Ends a hide when a newer fetch answers or the id
    /// shows up non-pending.
    private func visible(_ gen: Int, _ rows: [SwingSignal], nonPending: [String]) -> [SwingSignal] {
        let seen = Set(nonPending)
        hidden = hidden.filter { id, at in !(gen > at || seen.contains(id)) }
        return rows.filter { hidden[$0.id] == nil && !seen.contains($0.id) }
    }

    /// One poll cycle. A failure keeps the last good list and says so.
    func refresh() async {
        generation += 1
        let gen = generation
        let load = await fetch()
        if gen < applied { return }
        guard let current = state.value else { return }
        if load.pending == nil, load.approved == nil {
            var next = current
            next.refreshError = load.error.map(swingErrorText) ?? "null"
            state = .loaded(next)
            return
        }
        applied = gen
        state = .loaded(apply(current, gen, load))
    }

    // MARK: Decide / re-send

    func decide(_ signal: SwingSignal, _ decision: String, reason: String? = nil) async -> DecisionResult {
        guard let current = state.value,
              !current.deciding.contains(signal.id),
              hidden[signal.id] == nil
        else { return DecisionResult(.ignored, "") }
        var next = current
        next.deciding.insert(signal.id)
        state = .loaded(next)
        do {
            let receipt = try await source().decide(instanceId, signal.id, decision, reason: reason)
            hidden[signal.id] = generation
            forgetStuck(signal.id) // R3-2: a new decision forgets an old dismissal
            drop(signal.id)
            if receipt.uncertain {
                let message = receipt.detail.isEmpty ? uncertainMessage() : receipt.detail
                wait(signal)
                return DecisionResult(.uncertain, message)
            }
            return DecisionResult(.recorded, decisionSuccessMessage(signal, decision))
        } catch let err as ApiError {
            let code = err.statusCode
            let detail = err.message.trimmingCharacters(in: .whitespacesAndNewlines)
            if code == 400 || code == 404 || code == 409 {
                drop(signal.id)
                return DecisionResult(
                    .noLongerPending,
                    detail.isEmpty ? "This signal is no longer pending — it was decided elsewhere." : detail
                )
            }
            release(signal.id)
            if code == 401 {
                return DecisionResult(.failed, "Session expired — please sign in again.")
            }
            if code == 403 {
                return DecisionResult(.failed, detail.isEmpty ? "You are not allowed to decide this signal." : detail)
            }
            if code == 503 {
                // Provably not queued: the signal is pending, so a retry is safe.
                return DecisionResult(.failed, detail.isEmpty ? "Not queued — the signal is still pending; try again." : detail)
            }
            return DecisionResult(.failed, detail.isEmpty ? "Could not record that decision." : detail)
        } catch {
            release(signal.id)
            return DecisionResult(.failed, "Could not record that decision: \(swingErrorText(error))")
        }
    }

    /// Re-send a stuck approval (fix wave item 3).
    func resend(_ signal: SwingSignal) async -> DecisionResult {
        guard let current = state.value, !current.resending.contains(signal.id) else {
            return DecisionResult(.ignored, "")
        }
        var next = current
        next.resending.insert(signal.id)
        state = .loaded(next)
        do {
            let receipt = try await source().resend(instanceId, signal.id)
            snooze(signal.id)
            if receipt.uncertain {
                let message = receipt.detail.isEmpty ? uncertainMessage("Re-send") : receipt.detail
                wait(signal)
                return DecisionResult(.uncertain, message)
            }
            return DecisionResult(.recorded, "Re-sent \(signal.symbol) to the broker.")
        } catch let err as ApiError {
            let code = err.statusCode
            let detail = err.message.trimmingCharacters(in: .whitespacesAndNewlines)
            if code == 404 || code == 409 {
                snooze(signal.id)
                return DecisionResult(.noLongerPending, detail.isEmpty ? "Nothing to re-send for this signal." : detail)
            }
            releaseResend(signal.id)
            if code == 401 {
                return DecisionResult(.failed, "Session expired — please sign in again.")
            }
            if code == 503 {
                return DecisionResult(.failed, detail.isEmpty ? "Not queued — try again." : detail)
            }
            return DecisionResult(.failed, detail.isEmpty ? "Could not re-send that approval." : detail)
        } catch {
            releaseResend(signal.id)
            return DecisionResult(.failed, "Could not re-send that approval: \(swingErrorText(error))")
        }
    }

    // MARK: Card bookkeeping

    /// Round 4 (R3-2): a new decision or 202 on `id` starts it afresh.
    private func forgetStuck(_ id: String) {
        dismissedStuck.remove(id)
        joinedStuck.remove(id)
    }

    /// Follow-up 2: a 202 puts the signal on a waiting card.
    private func wait(_ signal: SwingSignal) {
        forgetStuck(signal.id)
        setUncertain(signal.id, UncertainCard(signal: signal, since: generation, sinceAt: clock()))
        var next = state.value ?? PendingSignalsState()
        next.uncertain = uncertainValues
        next.stuck = next.stuck.filter { $0.id != signal.id }
        state = .loaded(next)
    }

    /// Round 3 FU-1: takes a stuck card off for good on this device.
    func dismissStuck(_ id: String) {
        dismissedStuck.insert(id)
        var next = state.value ?? PendingSignalsState()
        next.stuck = next.stuck.filter { $0.id != id }
        state = .loaded(next)
    }

    /// Removes a waiting card (its Dismiss button).
    func dismissUncertain(_ id: String) {
        removeUncertain(id)
        var next = state.value ?? PendingSignalsState()
        next.uncertain = uncertainValues
        state = .loaded(next)
    }

    /// Takes the stuck card off and holds it back for another `stuckAfter`.
    private func snooze(_ id: String) {
        resentAt[id] = clock()
        var next = state.value ?? PendingSignalsState()
        next.stuck = next.stuck.filter { $0.id != id }
        next.resending.remove(id)
        state = .loaded(next)
    }

    private func releaseResend(_ id: String) {
        var next = state.value ?? PendingSignalsState()
        next.resending.remove(id)
        state = .loaded(next)
    }

    private func drop(_ id: String) {
        var next = state.value ?? PendingSignalsState()
        next.signals = next.signals.filter { $0.id != id }
        next.deciding.remove(id)
        state = .loaded(next)
    }

    private func release(_ id: String) {
        var next = state.value ?? PendingSignalsState()
        next.deciding.remove(id)
        state = .loaded(next)
    }
}

// MARK: - Wheel snapshot

/// `wheelSnapshotProvider`: the wheel lane's book, loaded on open and on
/// pull-to-refresh / Retry.
@Observable
final class WheelModel {
    let instanceId: String
    private(set) var state: Loadable<WheelSnapshot> = .loading

    @ObservationIgnored private let source: () -> any SwingSignalsSource

    init(instanceId: String, source: @escaping () -> any SwingSignalsSource) {
        self.instanceId = instanceId
        self.source = source
    }

    /// (Re)loads the book; the previous one stays visible meanwhile.
    func load() async {
        do {
            state = .loaded(try await source().wheel(instanceId))
        } catch {
            if !error.isCancellationOrTaskCancelled { state = .failed(error) }
        }
    }

    /// Retry (`ref.invalidate`): back to loading, then load.
    func retry() async {
        state = .loading
        await load()
    }
}
