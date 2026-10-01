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
    }

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

    init(uncertain: Bool = false, detail: String = "") {
        self.uncertain = uncertain
        self.detail = detail
    }

    init(json data: JSON) {
        if data.isObject {
            self.init(
                uncertain: data["uncertain"].bool,
                detail: swingStr(data["detail"]).trimmingCharacters(in: .whitespacesAndNewlines)
            )
        } else {
            self = .recorded
        }
    }
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

    /// POST .../decision with {decision, reason?}. `decision` is "approve" |
    /// "approve_half" | "reject". Throws `ApiError` on non-2xx.
    func decide(_ instanceId: String, _ signalId: String, _ decision: String, reason: String? = nil) async throws -> DecisionReceipt {
        var body: JSONObject = ["decision": .string(decision)]
        if let r = reason?.trimmingCharacters(in: .whitespacesAndNewlines), !r.isEmpty {
            body["reason"] = .string(r)
        }
        let data = try await client.post(
            "/instances/\(instanceId)/swing/signals/\(signalId)/decision",
            body: .object(body)
        )
        return DecisionReceipt(json: data)
    }

    func wheel(_ instanceId: String) async throws -> WheelSnapshot {
        let data = try await client.get("/instances/\(instanceId)/wheel")
        return data.isObject ? WheelSnapshot(json: data, fetchedAt: Date()) : .empty
    }
}
