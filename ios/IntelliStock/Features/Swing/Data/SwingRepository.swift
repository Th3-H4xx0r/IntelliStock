import Foundation

// Ported from features/swing/data/swing_repository.dart.
//
// Shapes: SwingSignals is section 5 of
// docs/superpowers/plans/2026-09-24-swing-port-interfaces.md; the wheel
// payload is the Contract addendum of
// docs/superpowers/plans/2026-09-24-swing-port-C-ui.md. If plan B ships a
// different wheel shape, WheelSnapshot.init(json:) is the only reader to
// change.

/// `_num`: a number's `toDouble()`, a string through `double.tryParse`.
nonisolated private func swingNum(_ v: JSON) -> Double? { v.numOrParsedDouble }

/// `_int`: a number's `toInt()`, a string through `int.tryParse`.
nonisolated private func swingInt(_ v: JSON) -> Int? {
    if let i = v.int { return i }
    if case .string(let s) = v { return JSON.parseInt(s) }
    return nil
}

/// `_str`: `''` for null, else `toString()`.
nonisolated private func swingStr(_ v: JSON) -> String { v.string ?? "" }

// MARK: - Models

/// One AI-scored candidate awaiting (or past) an operator decision.
nonisolated struct SwingSignal: Hashable, Sendable, Identifiable {
    let id: String
    /// "swing" | "wheel".
    let lane: String
    let symbol: String
    /// NY trading date, YYYY-MM-DD.
    let session: String
    let createdAt: String
    let score: Int?
    let recommendation: String
    let reasoning: String
    let keyRisks: [String]
    let sizeAdjustment: Double?
    let proposal: JSONObject
    let status: String
    /// When the operator decided it (UTC); nil while pending or unparseable.
    let decidedAt: Date?
    /// The client order id the broker wrote once it SENT the order (seams
    /// I-2). A submitted row without it is only the broker's claim, which can
    /// still go back to pending or be failed. nil when absent or empty.
    let orderClientId: String?
    /// When the broker claimed it (approved -> submitted). On a row that
    /// reads pending again, the broker refused the approval and put it back.
    let claimedAt: Date?
    /// The lane's scan context (stock_price, otm_pct, ...).
    let context: JSONObject
    /// The latest regrade of a wheel card; nil when it was never regraded
    /// or the block is not an object. A `let`, like every field: a row is
    /// one server snapshot, so the count an approval sends always belongs to
    /// the terms on screen.
    let regrade: SwingRegrade?
    /// "manual_scan" for a card a manual wheel run made; "" otherwise.
    let source: String

    init(json j: JSON) {
        id = swingStr(j["id"])
        lane = swingStr(j["lane"]).isEmpty ? "swing" : swingStr(j["lane"])
        symbol = swingStr(j["symbol"])
        session = swingStr(j["session"])
        createdAt = swingStr(j["created_at"])
        score = swingInt(j["score"])
        recommendation = swingStr(j["recommendation"])
        reasoning = swingStr(j["reasoning"])
        keyRisks = j["key_risks"].stringElements
        sizeAdjustment = swingNum(j["size_adjustment"])
        proposal = j["proposal"].orderedObjectValue
        status = swingStr(j["status"]).isEmpty ? "pending" : swingStr(j["status"])
        decidedAt = DartDateTime.tryParse(swingStr(j["decided_at"]))
        orderClientId = swingStr(j["order_client_id"]).isEmpty ? nil : swingStr(j["order_client_id"])
        claimedAt = DartDateTime.tryParse(swingStr(j["claimed_at"]))
        context = j["context"].orderedObjectValue
        regrade = j["regrade"].isObject ? SwingRegrade(json: j["regrade"]) : nil
        source = swingStr(j["source"])
    }

    /// Made by a manual "Run wheel now", not the weekly scan.
    var isManualRun: Bool { source == "manual_scan" }

    /// Only a pending wheel card can be regraded.
    var canRegrade: Bool { isWheel && status == "pending" }

    /// Pending again after a broker claim: an approval the broker refused.
    var returnedByBroker: Bool { status == "pending" && claimedAt != nil }

    var stockPrice: Double? { swingNum(context["stock_price"] ?? .null) }
    var otmPct: Double? { swingNum(context["otm_pct"] ?? .null) }

    var isWheel: Bool { lane == "wheel" }

    /// Approve-half exists for swing entries only; the wheel sizes in whole
    /// contracts.
    var allowsHalf: Bool { lane == "swing" }

    var keyRisksText: String {
        keyRisks
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
    }

    private func p(_ key: String) -> JSON { proposal[key] ?? .null }

    // Swing proposal
    var entry: Double? { swingNum(p("entry")) }
    var stop: Double? { swingNum(p("stop")) }
    var target: Double? { swingNum(p("target")) }
    var shares: Int? { swingInt(p("shares")) }

    // Wheel proposal
    var contract: String { swingStr(p("contract")) }
    var strike: Double? { swingNum(p("strike")) }
    var expiry: String { swingStr(p("expiry")) }
    var qty: Int? { swingInt(p("qty")) }
    var limitPrice: Double? { swingNum(p("limit_price")) }
    var premiumEst: Double? { swingNum(p("premium_est")) }

    /// Premium for the whole order: per-share premium x 100 x contracts.
    var creditEst: Double? {
        guard let premium = premiumEst, let q = qty else { return nil }
        return premium * 100 * Double(q)
    }

    /// Cash a sold put ties up: strike x 100 x contracts.
    var collateral: Double? {
        guard let s = strike, let q = qty else { return nil }
        return s * 100 * Double(q)
    }
}

/// What a wheel card said before its latest regrade (`regrade.previous`).
nonisolated struct SwingRegradePrevious: Hashable, Sendable {
    let score: Int?
    let recommendation: String
    let stockPrice: Double?
    let strike: Double?
    let premiumEst: Double?
    let expiry: String
    /// When that score was made.
    let at: Date?

    init(json j: JSON) {
        score = swingInt(j["score"])
        recommendation = swingStr(j["recommendation"])
        stockPrice = swingNum(j["stock_price"])
        strike = swingNum(j["strike"])
        premiumEst = swingNum(j["premium_est"])
        expiry = swingStr(j["expiry"])
        at = DartDateTime.tryParse(swingStr(j["at"]))
    }
}

/// A wheel card's `regrade` block (swing_trader/wheel_manual.py). Every
/// field is optional on the wire; a missing or mistyped one reads empty.
nonisolated struct SwingRegrade: Hashable, Sendable {
    /// queued | running | done | failed; "" when missing.
    let status: String
    let requestedAt: Date?
    let requestedBy: String
    let finishedAt: Date?
    /// Regrades completed.
    let count: Int
    let error: String
    /// Set by a finished regrade only.
    let qualifies: Bool?
    /// false: the name failed today's screen, so the score, strike and
    /// reasoning were left as they were.
    let rescored: Bool?
    /// Why it no longer qualifies.
    let reason: String
    /// When the card's current score was made.
    let scoredAt: Date?
    let previous: SwingRegradePrevious?

    init(json j: JSON) {
        status = swingStr(j["status"])
        requestedAt = DartDateTime.tryParse(swingStr(j["requested_at"]))
        requestedBy = swingStr(j["requested_by"])
        finishedAt = DartDateTime.tryParse(swingStr(j["finished_at"]))
        count = swingInt(j["count"]) ?? 0
        error = swingStr(j["error"]).trimmingCharacters(in: .whitespacesAndNewlines)
        qualifies = j["qualifies"].boolValue
        rescored = j["rescored"].boolValue
        reason = swingStr(j["reason"]).trimmingCharacters(in: .whitespacesAndNewlines)
        scoredAt = DartDateTime.tryParse(swingStr(j["scored_at"]))
        previous = j["previous"].isObject ? SwingRegradePrevious(json: j["previous"]) : nil
    }
}

/// One manual wheel run (`GET/POST /instances/{id}/wheel/scan`'s `run`).
nonisolated struct WheelRun: Hashable, Sendable {
    let id: String
    /// running | done | failed.
    let status: String
    /// queued | screening | earnings | scoring | done | failed.
    let phase: String
    let requestedBy: String
    let startedAt: Date?
    let finishedAt: Date?
    /// The NY session it scanned, YYYY-MM-DD; "" until the run sets it.
    let session: String
    let scanned: Int
    let passedScreen: Int
    let toScore: Int
    let scored: Int
    let newPending: Int
    let rejected: Int
    let skippedExisting: Int
    let errors: Int
    let error: String

    init(json j: JSON) {
        func count(_ key: String) -> Int { swingInt(j[key]) ?? 0 }
        id = swingStr(j["id"])
        status = swingStr(j["status"])
        phase = swingStr(j["phase"])
        requestedBy = swingStr(j["requested_by"])
        startedAt = DartDateTime.tryParse(swingStr(j["started_at"]))
        finishedAt = DartDateTime.tryParse(swingStr(j["finished_at"]))
        session = swingStr(j["session"])
        scanned = count("scanned")
        passedScreen = count("passed_screen")
        toScore = count("to_score")
        scored = count("scored")
        newPending = count("new_pending")
        rejected = count("rejected")
        skippedExisting = count("skipped_existing")
        errors = count("errors")
        error = swingStr(j["error"]).trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

/// `GET /instances/{id}/wheel/scan` (and the 200 of its POST): the newest
/// manual run and whether the server counts it as still going.
nonisolated struct WheelRunStatus: Hashable, Sendable {
    let run: WheelRun?
    let inProgress: Bool

    init(run: WheelRun? = nil, inProgress: Bool = false) {
        self.run = run
        self.inProgress = inProgress
    }

    /// Trusts the server's `in_progress` (it already counts a run whose
    /// heartbeat stopped as finished). A missing or junk run reads as none.
    init(json j: JSON) {
        self.init(
            run: j["run"].isObject ? WheelRun(json: j["run"]) : nil,
            inProgress: j["in_progress"].bool
        )
    }
}

/// One open cash-secured put.
nonisolated struct WheelPut: Hashable, Sendable {
    let contract: String
    let underlying: String
    let strike: Double?
    let expiry: String
    let qty: Int?
    let avgEntryPrice: Double?
    let currentPrice: Double?
    let underlyingPrice: Double?
    /// Percent the underlying sits below the strike; > 0 means in the money.
    let itmPct: Double?
    let dte: Int?
    let collateral: Double?
    let unrealizedPl: Double?

    init(
        contract: String,
        underlying: String,
        strike: Double? = nil,
        expiry: String,
        qty: Int? = nil,
        avgEntryPrice: Double? = nil,
        currentPrice: Double? = nil,
        underlyingPrice: Double? = nil,
        itmPct: Double? = nil,
        dte: Int? = nil,
        collateral: Double? = nil,
        unrealizedPl: Double? = nil
    ) {
        self.contract = contract
        self.underlying = underlying
        self.strike = strike
        self.expiry = expiry
        self.qty = qty
        self.avgEntryPrice = avgEntryPrice
        self.currentPrice = currentPrice
        self.underlyingPrice = underlyingPrice
        self.itmPct = itmPct
        self.dte = dte
        self.collateral = collateral
        self.unrealizedPl = unrealizedPl
    }

    init(json j: JSON) {
        self.init(
            contract: swingStr(j["contract"]),
            underlying: swingStr(j["underlying"]),
            strike: swingNum(j["strike"]),
            expiry: swingStr(j["expiry"]),
            qty: swingInt(j["qty"]),
            avgEntryPrice: swingNum(j["avg_entry_price"]),
            currentPrice: swingNum(j["current_price"]),
            underlyingPrice: swingNum(j["underlying_price"]),
            itmPct: swingNum(j["itm_pct"]),
            dte: swingInt(j["dte"]),
            collateral: swingNum(j["collateral"]),
            unrealizedPl: swingNum(j["unrealized_pl"])
        )
    }

    /// Exactly the puts the 15:45 ET monitor buys back (spec section 5.2).
    var monitorWillBuyBack: Bool {
        guard let itm = itmPct else { return false }
        let d = dte
        return itm >= 10 || (itm >= 5 && d != nil && d! <= 2) || (itm > 0 && d == 0)
    }
}

/// One row of the weekly wheel scan log (SwingWheelScans).
nonisolated struct WheelScan: Hashable, Sendable, Identifiable {
    let id: String
    let session: String
    let symbol: String
    let strike: Double?
    let expiry: String
    let score: Int?
    /// "placed" | "pending" | "rejected" | "skipped".
    let status: String
    let skipReason: String

    init(json j: JSON) {
        id = swingStr(j["id"])
        session = swingStr(j["session"])
        symbol = swingStr(j["symbol"])
        strike = swingNum(j["strike"])
        expiry = swingStr(j["expiry"])
        score = swingInt(j["score"])
        status = swingStr(j["status"])
        skipReason = swingStr(j["skip_reason"])
    }
}

/// `GET /instances/{id}/wheel`.
nonisolated struct WheelSnapshot: Hashable, Sendable {
    static let empty = WheelSnapshot()

    let openPuts: [WheelPut]
    let collateralTotal: Double?
    let cash: Double?
    let recentScans: [WheelScan]
    /// When this book was fetched (local time). The card loads only on open
    /// and pull-to-refresh, so it says how old the book is (FW item 4, M-3).
    let fetchedAt: Date?

    init(
        openPuts: [WheelPut] = [],
        collateralTotal: Double? = nil,
        cash: Double? = nil,
        recentScans: [WheelScan] = [],
        fetchedAt: Date? = nil
    ) {
        self.openPuts = openPuts
        self.collateralTotal = collateralTotal
        self.cash = cash
        self.recentScans = recentScans
        self.fetchedAt = fetchedAt
    }

    init(json j: JSON, fetchedAt: Date? = nil) {
        self.init(
            openPuts: j["open_puts"].objectElements.map(WheelPut.init(json:)),
            collateralTotal: swingNum(j["collateral_total"]),
            cash: swingNum(j["cash"]),
            recentScans: j["recent_scans"].objectElements.map(WheelScan.init(json:)),
            fetchedAt: fetchedAt
        )
    }
}

/// What a 2xx from POST .../decision said. FW-api-I1: a 202 carries
/// `{"uncertain": true, "detail"}` — the approval is recorded, but the broker
/// command may or may not be queued, so the order may be in flight.
nonisolated struct DecisionReceipt: Hashable, Sendable {
    static let recorded = DecisionReceipt()

    let uncertain: Bool
    let detail: String
    /// The broker command an approval queued (`command_id`); nil for a
    /// rejection or when the server had none.
    let commandId: String?

    init(uncertain: Bool = false, detail: String = "", commandId: String? = nil) {
        self.uncertain = uncertain
        self.detail = detail
        self.commandId = commandId
    }

    init(json data: JSON) {
        if data.isObject {
            let command = swingStr(data["command_id"])
            self.init(
                uncertain: data["uncertain"].bool,
                detail: swingStr(data["detail"]).trimmingCharacters(in: .whitespacesAndNewlines),
                commandId: command.isEmpty ? nil : command
            )
        } else {
            self = .recorded
        }
    }
}

/// `GET /live-commands/{id}`: where an approval's broker command stands.
nonisolated struct SwingCommandStatus: Hashable, Sendable {
    let id: String
    /// pending | running | completed | failed.
    let status: String
    /// The broker's reason when it failed ("order gate blocked: ...").
    let error: String

    init(json j: JSON) {
        id = swingStr(j["id"])
        status = swingStr(j["status"]).isEmpty ? "pending" : swingStr(j["status"])
        error = swingStr(j["error"]).trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var isTerminal: Bool { status == "completed" || status == "failed" }
}

// MARK: - Repository

nonisolated struct SwingRepository: Sendable {
    let client: ApiClient

    private func signals(_ instanceId: String, _ status: String) async throws -> [SwingSignal] {
        let data = try await client.get("/instances/\(instanceId)/swing/signals", query: ["status": .string(status)])
        let rows: JSON = data.isArray ? data : (data.isObject ? data["signals"] : .array([]))
        return rows.objectElements
            .map(SwingSignal.init(json:))
            .filter { !$0.id.isEmpty && $0.status == status }
    }

    /// Pending signals, newest first. Accepts a bare list or {signals: [...]}
    /// and drops anything not pending, in case an older API build ignores
    /// the status filter.
    func pendingSignals(_ instanceId: String) async throws -> [SwingSignal] {
        try await signals(instanceId, "pending").sorted { dartCompare($0.createdAt, $1.createdAt) > 0 }
    }

    /// Signals that read `status` (follow-up 2 reads "submitted" and
    /// "failed" while a card waits for the broker).
    func signalsWithStatus(_ instanceId: String, _ status: String) async throws -> [SwingSignal] {
        try await signals(instanceId, status)
    }

    /// Signals that still read approved or approved_half, newest decision
    /// first: the ones a broker command has not claimed yet (fix wave item 3).
    func approvedSignals(_ instanceId: String) async throws -> [SwingSignal] {
        async let approved = signals(instanceId, "approved")
        async let half = signals(instanceId, "approved_half")
        let rows = try await approved + half
        let epoch = Date(timeIntervalSince1970: 0)
        return rows.sorted { ($0.decidedAt ?? epoch) > ($1.decidedAt ?? epoch) }
    }

    /// POST .../resend: queue a stuck approval's broker command again. Throws
    /// `ApiError` on non-2xx (409 not approved or a command still queued,
    /// 503 not queued).
    func resend(_ instanceId: String, _ signalId: String) async throws -> DecisionReceipt {
        DecisionReceipt(json: try await client.post("/instances/\(instanceId)/swing/signals/\(signalId)/resend"))
    }

    /// POST .../decision with {decision, reason?, expect_regrade_count?}.
    /// `decision` is "approve" | "approve_half" | "reject".
    /// `expectRegradeCount` (a wheel approval) is the regrade count of the
    /// card the operator saw; the server answers 412 when the card has been
    /// regraded since. Throws `ApiError` on non-2xx.
    func decide(
        _ instanceId: String, _ signalId: String, _ decision: String,
        reason: String? = nil, expectRegradeCount: Int? = nil
    ) async throws -> DecisionReceipt {
        var body: JSONObject = ["decision": .string(decision)]
        if let r = reason?.trimmingCharacters(in: .whitespacesAndNewlines), !r.isEmpty {
            body["reason"] = .string(r)
        }
        if let expectRegradeCount { body["expect_regrade_count"] = .int(expectRegradeCount) }
        let data = try await client.post(
            "/instances/\(instanceId)/swing/signals/\(signalId)/decision",
            body: .object(body)
        )
        return DecisionReceipt(json: data)
    }

    /// `GET /live-commands/{id}`: an approval's broker command.
    func commandStatus(_ commandId: String) async throws -> SwingCommandStatus {
        SwingCommandStatus(json: try await client.get("/live-commands/\(commandId)"))
    }

    /// Every recent signal, any status (the wheel's scan rows link to them).
    func recentSignals(_ instanceId: String, limit: Int = 200) async throws -> [SwingSignal] {
        let data = try await client.get("/instances/\(instanceId)/swing/signals", query: ["limit": JSON(limit)])
        let rows: JSON = data.isArray ? data : (data.isObject ? data["signals"] : .array([]))
        return rows.objectElements.map(SwingSignal.init(json:)).filter { !$0.id.isEmpty }
    }

    func wheel(_ instanceId: String) async throws -> WheelSnapshot {
        let data = try await client.get("/instances/\(instanceId)/wheel")
        return data.isObject ? WheelSnapshot(json: data, fetchedAt: Date()) : .empty
    }

    /// POST .../regrade: re-score one pending wheel card at today's prices.
    /// Answers the signal with its regrade queued; the result lands on the
    /// row about a minute later. Throws `ApiError` on non-2xx (400 not a
    /// pending wheel signal, 404 unknown, 409 already under way, 503 the
    /// instance is not running or it could not be queued).
    func regradeSignal(_ instanceId: String, _ signalId: String) async throws -> SwingSignal {
        let data = try await client.post("/instances/\(instanceId)/swing/signals/\(signalId)/regrade")
        return SwingSignal(json: data["signal"])
    }

    /// POST .../wheel/scan: run the wheel scan now. It only adds review
    /// cards and never places an order. Throws `ApiError` on non-2xx (409 a
    /// run is under way, 503 not running or not queued).
    func startWheelRun(_ instanceId: String) async throws -> WheelRunStatus {
        WheelRunStatus(json: try await client.post("/instances/\(instanceId)/wheel/scan"))
    }

    /// GET .../wheel/scan: the newest manual run, or none.
    func wheelRunStatus(_ instanceId: String) async throws -> WheelRunStatus {
        WheelRunStatus(json: try await client.get("/instances/\(instanceId)/wheel/scan"))
    }
}
