import Observation
import SwiftUI

/// The confirmation and in-flight state behind the pending-signal actions.
/// The instance screen owns it and presents its confirmation, so the
/// alert lives on the screen, not on a list row that can scroll away.
@Observable
final class PendingSignalsActions {
    var confirm: ConfirmRequest?
    /// A confirmed decision or re-send is running: every card's actions stay
    /// inert until it lands (the confirm runner drops a second request).
    var confirmRunning = false
    /// Signal ids whose confirmed action is running (on top of the model's
    /// own deciding/resending guards).
    var inFlight: Set<String> = []
}

/// AI-scored swing and wheel candidates that wait for a human — the
/// `PendingSignalsSection` card on the instance detail screen (spec
/// 2026-09-24 section 10) — as list sections, one per signal (redesign spec
/// 2026-10-02): the figures in a `StatGrid`, the rationale in a disclosure,
/// then Approve and Reject as large bordered buttons side by side.
///
/// Approve / Approve ½ / Reject and Re-send are REAL trading actions: each
/// goes through its confirmation, verbatim, and its buttons stay inert while
/// the request is in flight. The screen runs the poll and presents the
/// confirmation (`actions.confirm`).
struct PendingSignalsSection: View {
    let model: PendingSignalsModel
    let actions: PendingSignalsActions
    /// Shows a result (Dart's SnackBar) on the screen's toast.
    let showToast: (Toast) -> Void

    static let footer = "Approval rebuilds the order at the live price."

    var body: some View {
        let count = model.state.value?.signals.count
        let title = count.map { "Pending AI signals (\($0))" } ?? "Pending AI signals"
        switch model.state {
        case .loading:
            Section(title) {
                Text("Loading…").foregroundStyle(.secondary)
            }
        case .failed:
            Section(title) {
                ErrorRow(message: model.state.errorMessage ?? "") {
                    Task { await model.build() }
                }
            }
        case .loaded(let state):
            loaded(state, title: title)
        }
    }

    @ViewBuilder
    private func loaded(_ state: PendingSignalsState, title: String) -> some View {
        let now = state.asOf ?? Date()
        let hasStatus = state.signals.isEmpty || state.refreshError != nil
        let hasTail = !state.uncertain.isEmpty || !state.stuck.isEmpty
        if hasStatus {
            Section {
                if let error = state.refreshError {
                    Text("Last refresh failed: \(error)")
                        .font(.footnote)
                        .foregroundStyle(DS.Palette.warning)
                }
                if state.signals.isEmpty {
                    Text("Nothing waiting for review.")
                        .foregroundStyle(.secondary)
                }
            } header: {
                Text(title)
            } footer: {
                if state.signals.isEmpty, !hasTail { Text(Self.footer) }
            }
        }
        ForEach(Array(state.signals.enumerated()), id: \.element.id) { i, s in
            Section {
                SwingSignalRows(
                    signal: s,
                    busy: state.isDeciding(s.id) || actions.inFlight.contains(s.id) || actions.confirmRunning,
                    onDecide: { decide(s, $0) }
                )
            } header: {
                if i == 0, !hasStatus { Text(title) }
            } footer: {
                if i == state.signals.count - 1, !hasTail { Text(Self.footer) }
            }
        }
        // 202'd approvals and re-sends: the card and its badge stay until a
        // poll settles them.
        if !state.uncertain.isEmpty {
            Section {
                ForEach(state.uncertain, id: \.signal.id) { card in
                    SwingUncertainRow(
                        card: card,
                        canDismiss: card.canDismiss(now),
                        onDismiss: { model.dismissUncertain(card.signal.id) }
                    )
                }
            } header: {
                Text("Waiting for the broker (\(state.uncertain.count))")
            } footer: {
                if state.stuck.isEmpty { Text(Self.footer) }
            }
        }
        // Approvals no broker command has claimed for 2+ minutes.
        if !state.stuck.isEmpty {
            Section {
                ForEach(state.stuck) { s in
                    SwingStuckRow(
                        signal: s,
                        label: stuckLabel(s, now),
                        blockedReason: resendBlockedReason(s, nyDate(now)),
                        busy: state.isResending(s.id) || actions.inFlight.contains(s.id) || actions.confirmRunning,
                        onResend: { resend(s) },
                        onDismiss: { model.dismissStuck(s.id) }
                    )
                }
            } header: {
                Text("Approved, not yet sent (\(state.stuck.count))")
            } footer: {
                Text(Self.footer)
            }
        }
    }

    // MARK: Actions

    /// The decision confirm, then the request; the result shows as a toast
    /// unless it was ignored or uncertain (that advice lives on its card).
    private func decide(_ signal: SwingSignal, _ decision: String) {
        let actions = actions
        let model = model
        let showToast = showToast
        actions.confirm = ConfirmRequest(
            title: "\(decisionLabel(decision)) \(signal.symbol)",
            body: decisionConfirmBody(signal, decision),
            confirmLabel: decisionLabel(decision),
            role: decision == "reject" ? .destructive : nil,
            onConfirm: {
                actions.inFlight.insert(signal.id)
                defer { actions.inFlight.remove(signal.id) }
                let result = await model.decide(signal, decision)
                Self.report(result, showToast)
            },
            onError: { showToast(Toast(swingErrorText($0), style: .error)) }
        )
    }

    private func resend(_ signal: SwingSignal) {
        let actions = actions
        let model = model
        let showToast = showToast
        actions.confirm = ConfirmRequest(
            title: "Re-send \(signal.symbol)",
            body: resendConfirmBody(signal),
            confirmLabel: "Re-send",
            role: nil,
            onConfirm: {
                actions.inFlight.insert(signal.id)
                defer { actions.inFlight.remove(signal.id) }
                let result = await model.resend(signal)
                Self.report(result, showToast)
            },
            onError: { showToast(Toast(swingErrorText($0), style: .error)) }
        )
    }

    private static func report(_ result: DecisionResult, _ showToast: (Toast) -> Void) {
        switch result.outcome {
        case .ignored, .uncertain:
            return
        case .recorded:
            showToast(Toast(result.message, style: .success))
        case .noLongerPending:
            showToast(Toast(result.message, style: .info))
        case .failed:
            showToast(Toast(result.message, style: .error))
        }
    }
}

/// A server label in sentence case ("ENTRY" → "Entry"); acronyms the
/// signal fields use stay upper case.
private func swingFieldLabel(_ label: String) -> String {
    let acronyms: Set<String> = ["DTE", "ITM", "P&L"]
    if acronyms.contains(label) { return label }
    return label.lowercased().dsSentenceCased
}

/// The symbol over its lane and session — what every card starts with.
private func swingSubtitle(_ s: SwingSignal) -> String {
    "\(s.lane.dsSentenceCased) · \(swingSessionText(s))"
}

/// One signal awaiting a decision (`_SignalCard`): its rows in the signal's
/// section.
private struct SwingSignalRows: View {
    let signal: SwingSignal
    let busy: Bool
    let onDecide: (String) -> Void

    var body: some View {
        let s = signal
        let reasoning = s.reasoning.trimmingCharacters(in: .whitespacesAndNewlines)
        EntityRow(s.symbol, subtitle: swingSubtitle(s))
        StatGrid(columns: 3) {
            StatCell(label: "Score", value: s.score.map(String.init) ?? "—", valueColor: swingScoreColor(s.score))
            ForEach(Array(swingProposalFields(s).enumerated()), id: \.offset) { _, field in
                StatCell(label: swingFieldLabel(field.label), value: field.value)
            }
        }
        .padding(.vertical, 4)
        if !reasoning.isEmpty {
            DisclosureGroup("Rationale") {
                Text(reasoning)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        if !s.keyRisksText.isEmpty {
            Text("Risks: \(s.keyRisksText)")
                .font(.footnote)
                .foregroundStyle(DS.Palette.warning)
        }
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Button { onDecide("approve") } label: {
                    Text(decisionLabel("approve")).frame(maxWidth: .infinity)
                }
                .tint(DS.Palette.success)
                if s.allowsHalf {
                    Button { onDecide("approve_half") } label: {
                        Text(decisionLabel("approve_half")).frame(maxWidth: .infinity)
                    }
                    .tint(DS.Palette.success)
                }
                Button(role: .destructive) { onDecide("reject") } label: {
                    Text(decisionLabel("reject")).frame(maxWidth: .infinity)
                }
                .tint(DS.Palette.danger)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
            .disabled(busy)
            if busy {
                Text("Working…")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
    }
}

/// An approval no broker command has claimed for 2+ minutes (`_StuckCard`).
private struct SwingStuckRow: View {
    let signal: SwingSignal
    let label: String
    let blockedReason: String?
    let busy: Bool
    let onResend: () -> Void
    let onDismiss: () -> Void

    var body: some View {
        let s = signal
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text(s.symbol)
                    .font(.headline)
                StatusBadge(label: s.status == "approved_half" ? "Approved ½" : "Approved", color: DS.Palette.warning)
                Spacer(minLength: 0)
            }
            Text(swingSubtitle(s))
                .font(.subheadline)
                .foregroundStyle(.secondary)
            Text(label)
                .font(.subheadline)
            if let blockedReason {
                Text(blockedReason)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            HStack(spacing: 20) {
                if blockedReason == nil {
                    Button("Re-send", action: onResend)
                }
                Button("Dismiss", action: onDismiss)
            }
            .buttonStyle(.borderless)
            .disabled(busy)
            .padding(.top, 2)
            if busy {
                Text("Working…")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
    }
}

/// A 202'd approval or re-send waiting for the broker (`_UncertainCardView`).
private struct SwingUncertainRow: View {
    let card: UncertainCard
    let canDismiss: Bool
    let onDismiss: () -> Void

    private var tone: Color {
        switch card.resolved {
        case "submitted": DS.Palette.success
        case "failed": DS.Palette.danger
        default: DS.Palette.warning
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text(card.signal.symbol)
                    .font(.headline)
                StatusBadge(label: card.badge.dsSentenceCased, color: tone)
                Spacer(minLength: 0)
            }
            Text(swingSubtitle(card.signal))
                .font(.subheadline)
                .foregroundStyle(.secondary)
            Text(swingUncertainCopy(card))
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            if canDismiss {
                Button("Dismiss", action: onDismiss)
                    .buttonStyle(.borderless)
                    .padding(.top, 2)
            }
        }
        .padding(.vertical, 4)
    }
}

/// The wheel lane's open cash-secured puts and latest scans (`WheelCard`),
/// as two list sections. Read-only: a red ITM figure means the 15:45 ET
/// monitor will buy that put back on its next pass. The screen runs the load.
struct WheelSections: View {
    let model: WheelModel

    var body: some View {
        Section {
            switch model.state {
            case .loading:
                Text("Loading…").foregroundStyle(.secondary)
            case .failed(let error):
                ErrorRow(message: wheelErrorMessage(error)) {
                    Task { await model.retry() }
                }
            case .loaded(let w):
                StatGrid(columns: 3) {
                    StatCell(label: "Open puts", value: "\(w.openPuts.count)")
                    StatCell(label: "Collateral", value: fmtMoney(w.collateralTotal))
                    StatCell(label: "Cash", value: fmtMoney(w.cash))
                }
                .padding(.vertical, 4)
                if w.openPuts.isEmpty {
                    Text("No open puts.").foregroundStyle(.secondary)
                } else {
                    ForEach(Array(w.openPuts.enumerated()), id: \.offset) { _, put in
                        WheelPutRow(put: put)
                    }
                }
            }
        } header: {
            DSSectionHeader("Wheel") {
                if let at = model.state.value?.fetchedAt {
                    Text(fmtAsOf(at))
                }
            }
        }
        if let w = model.state.value {
            Section("Recent scans") {
                if w.recentScans.isEmpty {
                    Text("No scans recorded yet.").foregroundStyle(.secondary)
                } else {
                    ForEach(Array(w.recentScans.prefix(5).enumerated()), id: \.offset) { _, scan in
                        WheelScanRow(scan: scan)
                    }
                }
            }
        }
    }
}

private struct WheelPutRow: View {
    let put: WheelPut

    private var itmColor: Color {
        guard let itm = put.itmPct else { return .secondary }
        if put.monitorWillBuyBack { return DS.Palette.danger }
        return itm > 0 ? DS.Palette.warning : DS.Palette.success
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                Text("\(put.underlying) \(fmtMoney(put.strike)) P · \(put.expiry)")
                    .font(.headline)
                Text(put.contract)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            StatGrid(columns: 3) {
                StatCell(label: "Qty", value: put.qty.map(String.init) ?? "—")
                StatCell(label: "Entry", value: fmtMoney(put.avgEntryPrice))
                StatCell(label: "Mark", value: fmtMoney(put.currentPrice))
                StatCell(label: "ITM", value: fmtItm(put.itmPct), valueColor: itmColor)
                StatCell(label: "DTE", value: put.dte.map(String.init) ?? "—")
                StatCell(
                    label: "P&L",
                    value: fmtPnl(put.unrealizedPl),
                    valueColor: put.unrealizedPl == nil ? .secondary : pnlColor(put.unrealizedPl)
                )
            }
        }
        .padding(.vertical, 4)
    }
}

private struct WheelScanRow: View {
    let scan: WheelScan

    private var statusColor: Color {
        switch scan.status {
        case "placed": DS.Palette.success
        case "pending": DS.Palette.warning
        case "rejected": DS.Palette.danger
        default: .secondary
        }
    }

    var body: some View {
        EntityRow(
            "\(scan.symbol) \(fmtMoney(scan.strike)) P · \(scan.expiry.isEmpty ? "—" : scan.expiry)",
            subtitle: scan.skipReason.isEmpty ? nil : scan.skipReason,
            subtitleLineLimit: 2
        ) {
            AppBadge(label: scan.status.isEmpty ? "—" : scan.status, color: statusColor)
        }
    }
}
