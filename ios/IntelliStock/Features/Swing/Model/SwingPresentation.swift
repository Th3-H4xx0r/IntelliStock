import SwiftUI

// The pure helpers of features/swing/presentation/pending_signals_section.dart
// and wheel_card.dart.

/// Reasoning longer than this starts collapsed behind "Show more".
nonisolated let swingReasoningCollapseChars = 240

/// Collapsed reasoning (the web's `reasoningPreview`): cut on a word
/// boundary when one is close, else hard, and end in "…".
nonisolated func swingReasoningPreview(_ text: String, limit: Int = swingReasoningCollapseChars) -> (text: String, truncated: Bool) {
    let full = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard full.count > limit else { return (full, false) }
    let cut = String(full.prefix(limit))
    var head = cut
    if let space = cut.lastIndex(of: " "), cut.distance(from: cut.startIndex, to: space) > Int(Double(limit) * 0.6) {
        head = String(cut[..<space])
    }
    let trimmed = head.replacingOccurrences(of: "\\s+$", with: "", options: .regularExpression)
    return ("\(trimmed)…", true)
}

/// (label, value) pairs for a signal's proposal grid (`proposalFields`).
nonisolated func swingProposalFields(_ s: SwingSignal) -> [(label: String, value: String)] {
    if s.isWheel {
        let credit = s.creditEst
        return [
            ("CONTRACT", s.contract.isEmpty ? "—" : s.contract),
            ("STRIKE", fmtMoney(s.strike)),
            ("EXPIRY", s.expiry.isEmpty ? "—" : s.expiry),
            ("QTY", s.qty.map(String.init) ?? "—"),
            ("LIMIT", fmtMoney(s.limitPrice)),
            ("PREMIUM", credit == nil ? fmtMoney(s.premiumEst) : "\(fmtMoney(s.premiumEst)) (\(fmtMoney(credit)))"),
            ("COLLATERAL", fmtMoney(s.collateral)),
        ]
    }
    return [
        ("ENTRY", fmtMoney(s.entry)),
        ("STOP", fmtMoney(s.stop)),
        ("TARGET", fmtMoney(s.target)),
        ("SHARES", s.shares.map(String.init) ?? "—"),
    ]
}

/// The score's text colour, as the web's `scoreClass`: a score is not a
/// gain or a loss, so it steps the zinc ramp — ≥ 75 zinc-200, ≥ 50 zinc-400,
/// lower or none zinc-500.
nonisolated func swingScoreColor(_ score: Int?) -> Color {
    guard let score else { return DS.Text.muted }
    if score >= 75 { return DS.Text.soft }
    if score >= 50 { return DS.Text.secondary }
    return DS.Text.muted
}

/// The order the review sends, named as the web's confirm button
/// (`reviewConfirmLabel`): "Approve sell 1 QCOM put", "Approve buy 12 AMD",
/// "Approve ½ · AMD". The swipe hint under the review card shows it.
nonisolated func swingReviewActionLabel(_ s: SwingSignal, _ decision: String) -> String {
    if decision == "approve_half" { return "Approve ½ · \(s.symbol)" }
    if s.isWheel {
        let n = max(1, s.qty ?? 1)
        return "Approve sell \(n) \(s.symbol) put\(n == 1 ? "" : "s")"
    }
    guard let shares = s.shares else { return "Approve buy \(s.symbol)" }
    return "Approve buy \(shares) \(s.symbol)"
}

/// `session X` / `session —`.
nonisolated func swingSessionText(_ s: SwingSignal) -> String {
    "session \(s.session.isEmpty ? "—" : s.session)"
}

/// The waiting card's copy for its state.
nonisolated func swingUncertainCopy(_ card: UncertainCard) -> String {
    switch card.resolved {
    case "submitted": "The broker submitted it. Check open orders for the fill."
    case "failed": "It failed at the broker; the live log says why."
    default: waitingCopy
    }
}

// MARK: - Approval outcomes (swing-approvals fix, 2026-10-02)

/// A broker refusal as the card shows it: a plain headline, then the
/// server's own words.
nonisolated struct SwingRefusalText: Hashable, Sendable {
    let headline: String
    let detail: String
}

/// The headline for a broker refusal (`LiveCommands.error`), keyed on the
/// gate codes and handler phrases `_execute_swing_approval` writes.
nonisolated func swingRefusalText(_ error: String) -> SwingRefusalText {
    let e = error.lowercased()
    let headline: String
    if e.contains("dependency.watchdog") {
        headline = "The order gate's safety watchdog has stopped reporting, so the broker refuses every new order. Approving again won't help until it reports again; restarting the instance restarts it."
    } else if e.contains("collateral_insufficient") || e.contains("cash.insufficient") {
        headline = "Not enough cash to secure this put."
    } else if e.contains("underlying_cap") {
        headline = "This put would tie up more of the account in one stock than the lane allows."
    } else if e.contains("market.closed") || e.contains("regular_hours_required") || e.contains("after the close") {
        headline = "The market is closed. Approve again during regular hours."
    } else if e.contains("quote.stale") || e.contains("no live price") || e.contains("no usable options snapshot") {
        headline = "There was no fresh quote. Approve again once the market is open."
    } else if e.contains("no put contracts found") {
        headline = "No put contract matched this strike and expiry."
    } else if e.contains("approve a fresh signal") {
        headline = "This approval is from an earlier day. Approve a fresh signal."
    } else {
        headline = "The broker did not send it."
    }
    return SwingRefusalText(headline: headline, detail: error)
}

/// The note on a pending card the broker claimed and put back while this
/// device was not following it; nil for a fresh signal.
nonisolated func swingReturnedNote(_ s: SwingSignal, calendar: Calendar = DartDateTime.localCalendar) -> String? {
    guard s.returnedByBroker, let at = s.claimedAt else { return nil }
    let c = calendar.dateComponents([.month, .day, .hour, .minute], from: at)
    let months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    let month = months[max(0, min(11, (c.month ?? 1) - 1))]
    func two(_ v: Int) -> String { v < 10 ? "0\(v)" : "\(v)" }
    return "You approved this before, but the broker sent no order and returned it on \(month) \(c.day ?? 0) at \(two(c.hour ?? 0)):\(two(c.minute ?? 0)). The live log below says why."
}

/// A wheel put whose proposed collateral is above the account's cash.
nonisolated func swingCollateralWarning(_ s: SwingSignal, cash: Double?) -> String? {
    guard s.isWheel, let collateral = s.collateral, let cash, collateral > cash else { return nil }
    return "Collateral \(fmtMoney(collateral)) is more than the account's \(fmtMoney(cash)) cash. The broker picks the strike again when you approve and refuses the order if the cash can't secure it."
}

/// The two figures a put seller weighs first.
nonisolated struct SwingWheelLead: Hashable, Sendable {
    let premium: String
    let premiumFootnote: String?
    let collateral: String
    let collateralFootnote: String?
}

nonisolated func swingWheelLead(_ s: SwingSignal) -> SwingWheelLead {
    let credit = s.creditEst
    let ret: String? = {
        guard let credit, let collateral = s.collateral, collateral > 0 else { return nil }
        return "\(dartToStringAsFixed(credit / collateral * 100, 2))% return"
    }()
    return SwingWheelLead(
        premium: fmtMoney(credit ?? s.premiumEst),
        premiumFootnote: s.premiumEst.map { "\(fmtMoney($0)) a share" },
        collateral: fmtMoney(s.collateral),
        collateralFootnote: ret
    )
}

/// The rest of a wheel proposal. The broker resolves the contract and the
/// limit when you approve, so a scan row has neither yet.
nonisolated func swingWheelDetails(_ s: SwingSignal) -> [(label: String, value: String)] {
    [
        ("Strike", fmtMoney(s.strike)),
        ("Expiry", s.expiry.isEmpty ? "—" : s.expiry),
        ("Contracts", s.qty.map(String.init) ?? "—"),
        ("Contract", s.contract.isEmpty ? "Picked at approval" : s.contract),
        ("Limit", s.limitPrice == nil ? "Set at approval" : fmtMoney(s.limitPrice)),
    ]
}

nonisolated func swingScoreLabel(_ score: Int?) -> String {
    score.map { "Score \($0)" } ?? "No score"
}

nonisolated func swingLaneLabel(_ s: SwingSignal) -> String {
    s.isWheel ? "Wheel" : "Swing"
}

// MARK: - Wheel scan rows

/// The signal a scan row produced: the same wheel symbol, session and strike.
nonisolated func wheelScanSignal(_ scan: WheelScan, in signals: [SwingSignal]) -> SwingSignal? {
    signals.first { s in
        guard s.isWheel, s.symbol == scan.symbol, s.session == scan.session else { return false }
        switch (s.strike, scan.strike) {
        case (nil, nil): return true
        case let (a?, b?): return abs(a - b) < 0.005
        default: return false
        }
    }
}

nonisolated enum WheelScanTone: Hashable, Sendable { case good, waiting, bad, neutral }

nonisolated struct WheelScanStatus: Hashable, Sendable {
    let label: String
    let tone: WheelScanTone
    /// Pending in the live queue: the row jumps to its signal card.
    let reviewable: Bool
}

/// A scan row's badge: its signal's live status when it has one, else the
/// scan log's own.
nonisolated func wheelScanStatus(_ scan: WheelScan, signal: SwingSignal?, pendingIds: Set<String>) -> WheelScanStatus {
    if let signal {
        if pendingIds.contains(signal.id) { return WheelScanStatus(label: "Pending", tone: .waiting, reviewable: true) }
        switch signal.status {
        case "pending": return WheelScanStatus(label: "Decided", tone: .neutral, reviewable: false)
        case "rejected": return WheelScanStatus(label: "Rejected", tone: .bad, reviewable: false)
        case "failed": return WheelScanStatus(label: "Failed", tone: .bad, reviewable: false)
        case "auto_approved": return WheelScanStatus(label: "Auto-approved", tone: .good, reviewable: false)
        case "approved_half": return WheelScanStatus(label: "Approved ½", tone: .good, reviewable: false)
        case "submitted", "approved", "placed": return WheelScanStatus(label: signal.status.dsSentenceCased, tone: .good, reviewable: false)
        default: return WheelScanStatus(label: signal.status.dsSentenceCased, tone: .neutral, reviewable: false)
        }
    }
    switch scan.status {
    case "pending": return WheelScanStatus(label: "Pending", tone: .waiting, reviewable: false)
    case "placed": return WheelScanStatus(label: "Placed", tone: .good, reviewable: false)
    case "rejected": return WheelScanStatus(label: "Rejected", tone: .bad, reviewable: false)
    case "": return WheelScanStatus(label: "—", tone: .neutral, reviewable: false)
    default: return WheelScanStatus(label: scan.status.dsSentenceCased, tone: .neutral, reviewable: false)
    }
}

/// "2.0% ITM" / "3.0% OTM" / "—".
nonisolated func fmtItm(_ itmPct: Double?) -> String {
    guard let itmPct else { return "—" }
    return itmPct > 0
        ? "\(dartToStringAsFixed(itmPct, 1))% ITM"
        : "\(dartToStringAsFixed(abs(itmPct), 1))% OTM"
}

// MARK: Wheel put rows

/// "QCOM $177.50 Put", from the row's fields or, failing those, its OCC code.
nonisolated func wheelPutTitle(_ put: WheelPut) -> String {
    let occ = parseOccSymbol(put.contract)
    let underlying = put.underlying.isEmpty ? (occ?.underlying ?? put.contract) : put.underlying
    return "\(underlying) \(fmtMoney(put.strike ?? occ?.strike)) Put"
}

/// "7 days left · 4.1% above strike": the time left, then where the stock
/// sits against the strike (`itmPct` > 0 is below it, in the money).
nonisolated func wheelPutSubtitle(_ put: WheelPut) -> String {
    var parts: [String] = []
    switch put.dte {
    case .some(let d) where d <= 0: parts.append("Expires today")
    case .some(1): parts.append("1 day left")
    case .some(let d): parts.append("\(d) days left")
    case .none: if !put.expiry.isEmpty { parts.append("Expires \(put.expiry)") }
    }
    if let itm = put.itmPct {
        let pct = dartToStringAsFixed(abs(itm), 1)
        if pct == "0.0" {
            parts.append("At the strike")
        } else {
            parts.append(itm > 0 ? "\(pct)% below strike" : "\(pct)% above strike")
        }
    }
    return parts.joined(separator: " · ")
}

/// The put as the stock page's position: short (`qty` < 0) and valued at
/// the mark. nil without a contract count.
nonisolated func wheelPutPosition(_ put: WheelPut) -> AccountPosition? {
    guard let n = put.qty, n > 0, !put.contract.isEmpty else { return nil }
    let shares = Double(n * kOptionMultiplier)
    let pl = put.unrealizedPl ?? 0
    let cost = (put.avgEntryPrice ?? 0) * shares
    return AccountPosition(
        symbol: put.contract,
        qty: -Double(n),
        marketValue: -(put.currentPrice ?? 0) * shares,
        unrealizedPnl: pl,
        unrealizedPnlPct: cost > 0 ? pl / cost * 100 : 0,
        lastPrice: put.currentPrice,
        avgEntryPrice: put.avgEntryPrice
    )
}

/// The Wheel section's footer: "1 open put · $17,750.00 held as collateral."
nonisolated func wheelBookFooter(puts: Int, collateral: Double?) -> String {
    let count = puts == 1 ? "1 open put" : "\(puts) open puts"
    guard let collateral else { return "\(count)." }
    return "\(count) · \(fmtMoney(collateral)) held as collateral."
}

/// "as of 14:02", local 24-hour time; empty without a time.
nonisolated func fmtAsOf(_ at: Date?, calendar: Calendar = DartDateTime.localCalendar) -> String {
    guard let at else { return "" }
    let c = calendar.dateComponents([.hour, .minute], from: at)
    func two(_ v: Int) -> String { v < 10 ? "0\(v)" : "\(v)" }
    return "as of \(two(c.hour ?? 0)):\(two(c.minute ?? 0))"
}

/// What a failed wheel load says: FastAPI's own 404 for a missing route
/// reads exactly "Not Found"; any other error shows its text.
nonisolated func wheelErrorMessage(_ error: any Error) -> String {
    if let api = error as? ApiError, api.statusCode == 404,
       api.message.trimmingCharacters(in: .whitespacesAndNewlines) == "Not Found" {
        return "This API build has no wheel endpoint yet."
    }
    return swingErrorText(error)
}


// MARK: - Wheel regrade (swing_trader/wheel_manual.py)

/// A queued or running regrade older than this is dead (the server's
/// `REGRADE_STALE_S`): shown as failed, and it can be asked for again.
nonisolated let swingRegradeStaleAfter: TimeInterval = 300

/// Where a card's latest regrade stands.
nonisolated enum SwingRegradePhase: Hashable, Sendable {
    /// Never regraded, or a status this build does not know.
    case none
    /// Queued or running, asked under `swingRegradeStaleAfter` ago.
    case live
    case done
    case failed
    /// Queued or running too long (or undated): treated as failed.
    case stale
}

nonisolated func swingRegradePhase(_ s: SwingSignal, now: Date) -> SwingRegradePhase {
    guard let r = s.regrade else { return .none }
    switch r.status {
    case "queued", "running":
        guard let at = r.requestedAt else { return .stale }
        return now.timeIntervalSince(at) < swingRegradeStaleAfter ? .live : .stale
    case "done": return .done
    case "failed": return .failed
    default: return .none
    }
}

/// The line under a live regrade.
nonisolated let swingRegradeProgressText = "Regrading at today's prices…"

/// New York, for "Scored Mon 16 Nov, 10:42 ET".
nonisolated let swingNYCalendar: Calendar = {
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = TimeZone(identifier: "America/New_York")!
    return calendar
}()

nonisolated private let swingWeekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
nonisolated private let swingMonths = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

/// "20 Nov" in `calendar`'s zone.
nonisolated private func swingDayMonth(_ at: Date, _ calendar: Calendar) -> String {
    let c = calendar.dateComponents([.month, .day], from: at)
    return "\(c.day ?? 0) \(swingMonths[max(0, min(11, (c.month ?? 1) - 1))])"
}

/// "Mon 16 Nov, 10:42 ET".
nonisolated private func swingNYStamp(_ at: Date, _ calendar: Calendar) -> String {
    let c = calendar.dateComponents([.weekday, .hour, .minute], from: at)
    let weekday = swingWeekdays[max(0, min(6, (c.weekday ?? 1) - 1))]
    func two(_ v: Int) -> String { v < 10 ? "0\(v)" : "\(v)" }
    return "\(weekday) \(swingDayMonth(at, calendar)), \(two(c.hour ?? 0)):\(two(c.minute ?? 0)) ET"
}

/// "Scored Mon 16 Nov, 10:42 ET · stock at $140.00": when the card's
/// current score was made (`regrade.scored_at`, which a rescored regrade
/// sets and every later block carries; else `created_at`), in New York, and
/// the stock price it was made at.
nonisolated func swingScoredAtText(_ s: SwingSignal, calendar: Calendar = swingNYCalendar) -> String {
    let at = s.regrade?.scoredAt ?? DartDateTime.tryParse(s.createdAt)
    let when = at.map { "Scored \(swingNYStamp($0, calendar))" } ?? "Score time unknown"
    guard let price = s.stockPrice else { return when }
    return "\(when) · stock at \(fmtMoney(price))"
}

/// One before → after line of a rescored regrade.
nonisolated struct SwingRegradeChange: Hashable, Sendable {
    let label: String
    let before: String
    let after: String
    /// The percent move, for the stock price.
    let move: String?
}

/// A finished, rescored regrade's before → after lines: score, stock price
/// (with its move), strike and premium. Empty otherwise: a regrade that did
/// not rescore changed nothing on the card.
nonisolated func swingRegradeChanges(_ s: SwingSignal) -> [SwingRegradeChange] {
    guard let r = s.regrade, r.status == "done", r.rescored == true else { return [] }
    let p = r.previous
    func score(_ v: Int?) -> String { v.map(String.init) ?? "—" }
    return [
        SwingRegradeChange(label: "Score", before: score(p?.score), after: score(s.score), move: nil),
        SwingRegradeChange(
            label: "Stock", before: fmtMoney(p?.stockPrice), after: fmtMoney(s.stockPrice),
            move: swingPctMove(p?.stockPrice, s.stockPrice)
        ),
        SwingRegradeChange(label: "Strike", before: fmtMoney(p?.strike), after: fmtMoney(s.strike), move: nil),
        SwingRegradeChange(label: "Premium", before: fmtMoney(p?.premiumEst), after: fmtMoney(s.premiumEst), move: nil),
    ]
}

/// "+8.0%" / "-3.2%" / "0.0%" from `before` to `after`; nil without both
/// or from a price at or below zero.
nonisolated func swingPctMove(_ before: Double?, _ after: Double?) -> String? {
    guard let before, let after, before > 0 else { return nil }
    let pct = (after - before) / before * 100
    let text = dartToStringAsFixed(abs(pct), 1)
    if text == "0.0" { return "0.0%" }
    return "\(pct > 0 ? "+" : "-")\(text)%"
}

/// The card's warnings, in order: "No longer qualifies" whenever
/// `qualifies` is false (later requests and failures carry it until a done
/// regrade overwrites it), then a failed regrade or one that never finished.
/// The card, its old values and Approve / Reject all stay.
nonisolated func swingRegradeWarnings(_ s: SwingSignal, now: Date) -> [String] {
    guard let r = s.regrade else { return [] }
    var out: [String] = []
    if r.qualifies == false {
        out.append(r.reason.isEmpty ? "No longer qualifies" : "No longer qualifies: \(r.reason)")
    }
    switch swingRegradePhase(s, now: now) {
    case .failed:
        out.append(r.error.isEmpty ? "Regrade failed" : "Regrade failed: \(r.error)")
    case .stale:
        out.append("Regrade failed: the instance did not finish it within 5 minutes. You can regrade again")
    case .none, .live, .done:
        break
    }
    return out
}

/// The `expect_regrade_count` an approval sends: the regrade count (0 when
/// never regraded) of the wheel card the operator is looking at, so the
/// server can refuse (412) an approval of terms that have since changed. nil
/// for a rejection or a swing card.
nonisolated func swingExpectRegradeCount(_ s: SwingSignal, _ decision: String) -> Int? {
    guard s.isWheel, decision == "approve" || decision == "approve_half" else { return nil }
    return s.regrade?.count ?? 0
}

/// A failed regrade or run request in plain words: 409 `alreadyUnderWay`,
/// 503 "The instance is not running" and the server's detail, anything
/// else the detail itself.
nonisolated func swingActionErrorText(_ error: any Error, alreadyUnderWay: String) -> String {
    guard let api = error as? ApiError else { return swingErrorText(error) }
    let detail = api.message.trimmingCharacters(in: .whitespacesAndNewlines)
    switch api.statusCode {
    case 409:
        return alreadyUnderWay
    case 503:
        return detail.isEmpty ? "The instance is not running" : "The instance is not running: \(detail)"
    default:
        if !detail.isEmpty { return detail }
        return api.statusCode.map { "The server responded with status \($0)." } ?? "Something went wrong."
    }
}

// MARK: - Manual wheel run

/// The "Run wheel now" confirmation: it costs one AI call per candidate and
/// only ever makes review cards.
nonisolated let wheelRunConfirmMessage = "Scores today's wheel candidates with the AI, one call per candidate. "
    + "Places no orders: new picks wait here as cards for your review."

nonisolated private func wheelCards(_ n: Int) -> String { n == 1 ? "1 new card" : "\(n) new cards" }

/// What a run in progress is doing, by phase, with its counts.
nonisolated func wheelRunProgress(_ run: WheelRun) -> String {
    switch run.phase {
    case "queued":
        return "Waiting for the instance to start the run…"
    case "screening":
        return "Screening the wheel universe…"
    case "earnings":
        return "Checking earnings dates · \(run.passedScreen) of \(run.scanned) passed the screen"
    case "scoring":
        let found = run.newPending > 0 ? "\(wheelCards(run.newPending)) so far" : "no new cards yet"
        return "Scoring \(run.scored) of \(run.toScore) · \(found)"
    default:
        return "Starting the run…"
    }
}

/// The last run, once the server says it is not in progress: its counts,
/// its error, or that it stopped before it finished. "" without a status.
nonisolated func wheelRunSummary(_ run: WheelRun, calendar: Calendar = swingNYCalendar) -> String {
    let day: String = {
        let parts = run.session.split(separator: "-").compactMap { Int($0) }
        if parts.count == 3, (1...12).contains(parts[1]) { return "\(parts[2]) \(swingMonths[parts[1] - 1])" }
        return run.startedAt.map { swingDayMonth($0, calendar) } ?? ""
    }()
    let label = day.isEmpty ? "Manual run" : "Manual run \(day)"
    switch run.status {
    case "done":
        let errors = run.errors == 1 ? "1 error" : "\(run.errors) errors"
        return "\(label): scanned \(run.scanned), passed screen \(run.passedScreen), scored \(run.scored), "
            + "\(wheelCards(run.newPending)), \(run.rejected) rejected, "
            + "\(run.skippedExisting) already had a card, \(errors)"
    case "failed":
        return run.error.isEmpty ? "\(label) failed" : "\(label) failed: \(run.error)"
    case "running":
        return "\(label) stopped before it finished. You can run it again"
    default:
        return ""
    }
}
