import SwiftUI

// The pure helpers of features/swing/presentation/pending_signals_section.dart
// and wheel_card.dart.

/// Reasoning longer than this starts collapsed behind "Show more".
nonisolated let swingReasoningCollapseChars = 240

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

/// `_scoreColor`: ≥ 75 green, ≥ 50 amber, else red; nil secondary.
nonisolated func swingScoreColor(_ score: Int?) -> Color {
    guard let score else { return .secondary }
    if score >= 75 { return DS.Palette.success }
    if score >= 50 { return DS.Palette.warning }
    return DS.Palette.danger
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
