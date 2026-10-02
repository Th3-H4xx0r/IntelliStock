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
