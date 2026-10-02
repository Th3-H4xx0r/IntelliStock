import SwiftUI

/// AI-scored swing and wheel candidates that wait for a human — the
/// `PendingSignalsSection` card on the instance detail screen (spec
/// 2026-09-24 section 10). Approve / Approve ½ / Reject and Re-send are
/// REAL trading actions: each goes through its confirmation, verbatim, and
/// its buttons stay inert while the request is in flight.
struct PendingSignalsSection: View {
    let model: PendingSignalsModel
    /// Shows a result (Dart's SnackBar) on the screen's toast.
    let showToast: (Toast) -> Void

    @Environment(AppServices.self) private var services
    @State private var confirm: ConfirmRequest?
    /// A confirmed decision or re-send is running: every card's actions stay
    /// inert until it lands (the confirm runner drops a second request).
    @State private var confirmRunning = false
    /// Signal ids whose confirmed action is running (on top of the model's
    /// own deciding/resending guards).
    @State private var inFlight: Set<String> = []

    var body: some View {
        let count = model.state.value?.signals.count
        Card(padding: 16) {
            VStack(alignment: .leading, spacing: 10) {
                Text(count.map { "Pending AI signals (\($0))" } ?? "Pending AI signals")
                    .font(.subheadline.weight(.semibold))
                    .accessibilityAddTraits(.isHeader)
                switch model.state {
                case .loading:
                    Text("Loading…")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                case .failed:
                    ErrorRow(message: model.state.errorMessage ?? "") {
                        Task { await model.build() }
                    }
                case .loaded(let state):
                    loaded(state)
                }
            }
        }
        .confirmAlert($confirm, isRunning: $confirmRunning)
        .task { await model.poll(lifecycle: services.lifecycle) }
    }

    @ViewBuilder
    private func loaded(_ state: PendingSignalsState) -> some View {
        let now = state.asOf ?? Date()
        if let error = state.refreshError {
            Text("Last refresh failed: \(error)")
                .font(.caption2)
                .foregroundStyle(DS.Palette.warning)
        }
        if state.signals.isEmpty {
            Text("Nothing waiting for review.")
                .font(.footnote)
                .italic()
                .foregroundStyle(.secondary)
        } else {
            ForEach(state.signals) { s in
                SwingSignalCard(
                    signal: s,
                    busy: state.isDeciding(s.id) || inFlight.contains(s.id) || confirmRunning,
                    onDecide: { decide(s, $0) }
                )
                .id(s.id)
            }
        }
        // 202'd approvals and re-sends: the card and its badge stay until a
        // poll settles them.
        if !state.uncertain.isEmpty {
            SwingGroupLabel(text: "Waiting for the broker (\(state.uncertain.count))")
            ForEach(state.uncertain, id: \.signal.id) { card in
                SwingUncertainCardView(
                    card: card,
                    canDismiss: card.canDismiss(now),
                    onDismiss: { model.dismissUncertain(card.signal.id) }
                )
            }
        }
        // Approvals no broker command has claimed for 2+ minutes.
        if !state.stuck.isEmpty {
            SwingGroupLabel(text: "Approved, not yet sent (\(state.stuck.count))")
            ForEach(state.stuck) { s in
                SwingStuckCard(
                    signal: s,
                    label: stuckLabel(s, now),
                    blockedReason: resendBlockedReason(s, nyDate(now)),
                    busy: state.isResending(s.id) || inFlight.contains(s.id) || confirmRunning,
                    onResend: { resend(s) },
                    onDismiss: { model.dismissStuck(s.id) }
                )
            }
        }
        Text("Approval rebuilds the order at the live price.")
            .font(.caption2)
            .foregroundStyle(.tertiary)
            .padding(.top, 4)
    }

    // MARK: Actions

    /// The decision confirm, then the request; the result shows as a toast
    /// unless it was ignored or uncertain (that advice lives on its card).
    private func decide(_ signal: SwingSignal, _ decision: String) {
        confirm = ConfirmRequest(
            title: "\(decisionLabel(decision)) \(signal.symbol)",
            body: decisionConfirmBody(signal, decision),
            confirmLabel: decisionLabel(decision),
            role: decision == "reject" ? .destructive : nil,
            onConfirm: {
                inFlight.insert(signal.id)
                defer { inFlight.remove(signal.id) }
                let result = await model.decide(signal, decision)
                report(result)
            },
            onError: { showToast(Toast(swingErrorText($0), style: .error)) }
        )
    }

    private func resend(_ signal: SwingSignal) {
        confirm = ConfirmRequest(
            title: "Re-send \(signal.symbol)",
            body: resendConfirmBody(signal),
            confirmLabel: "Re-send",
            role: nil,
            onConfirm: {
                inFlight.insert(signal.id)
                defer { inFlight.remove(signal.id) }
                let result = await model.resend(signal)
                report(result)
            },
            onError: { showToast(Toast(swingErrorText($0), style: .error)) }
        )
    }

    private func report(_ result: DecisionResult) {
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

private struct SwingGroupLabel: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.caption2.weight(.semibold))
            .tracking(0.6)
            .foregroundStyle(DS.Palette.warning)
            .padding(.top, 8)
    }
}

/// The lane and score badges shared by every card.
private struct SwingCardHeader: View {
    let signal: SwingSignal
    var showScore = true

    var body: some View {
        HStack(spacing: 8) {
            Text(signal.symbol)
                .font(.subheadline.weight(.heavy))
            AppBadge(label: signal.lane, color: signal.isWheel ? DS.Palette.accent : DS.Palette.info)
            if showScore {
                AppBadge(label: signal.score.map(String.init) ?? "—", color: swingScoreColor(signal.score))
            }
        }
    }
}

/// One signal awaiting a decision (`_SignalCard`).
private struct SwingSignalCard: View {
    let signal: SwingSignal
    let busy: Bool
    let onDecide: (String) -> Void

    @State private var expanded = false

    var body: some View {
        let s = signal
        let reasoning = s.reasoning.trimmingCharacters(in: .whitespacesAndNewlines)
        let long = s.reasoning.count > swingReasoningCollapseChars
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                SwingCardHeader(signal: s).fixedSize()
                Spacer(minLength: 8)
                Text(swingSessionText(s))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
            }
            DashboardFlowLayout(spacing: 16) {
                ForEach(Array(swingProposalFields(s).enumerated()), id: \.offset) { _, field in
                    VStack(alignment: .leading, spacing: 1) {
                        Text(field.label)
                            .font(.caption2)
                            .tracking(0.4)
                            .foregroundStyle(.secondary)
                        Text(field.value)
                            .font(.caption.weight(.bold).monospacedDigit())
                    }
                    .accessibilityElement(children: .combine)
                }
            }
            if !reasoning.isEmpty {
                VStack(alignment: .leading, spacing: 0) {
                    Text(reasoning)
                        .font(.caption)
                        .lineLimit(expanded || !long ? nil : 4)
                    if long {
                        Button(expanded ? "Show less" : "Show more") { expanded.toggle() }
                            .font(.caption.weight(.semibold))
                            .buttonStyle(.borderless)
                            .frame(minHeight: 44)
                    }
                }
            }
            if !s.keyRisksText.isEmpty {
                Text("Risks: \(s.keyRisksText)")
                    .font(.caption2)
                    .foregroundStyle(DS.Palette.warning)
            }
            DashboardFlowLayout(spacing: 8) {
                Button(decisionLabel("approve")) { onDecide("approve") }
                    .buttonStyle(.bordered)
                    .tint(DS.Palette.success)
                if s.allowsHalf {
                    Button(decisionLabel("approve_half")) { onDecide("approve_half") }
                        .buttonStyle(.bordered)
                        .tint(DS.Palette.success)
                }
                Button(decisionLabel("reject"), role: .destructive) { onDecide("reject") }
                    .buttonStyle(.bordered)
            }
            .controlSize(.small)
            .disabled(busy)
            if busy {
                Text("Working…")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(12)
        .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
    }
}

/// An approval no broker command has claimed for 2+ minutes (`_StuckCard`).
private struct SwingStuckCard: View {
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
                SwingCardHeader(signal: s, showScore: false)
                AppBadge(label: s.status == "approved_half" ? "approved ½" : "approved", color: DS.Palette.warning)
            }
            Text(swingSessionText(s))
                .font(.caption2)
                .foregroundStyle(.tertiary)
            Text(label)
                .font(.caption)
            if let blockedReason {
                Text(blockedReason)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            HStack(spacing: 8) {
                if blockedReason == nil {
                    Button("Re-send", action: onResend)
                        .buttonStyle(.bordered)
                        .tint(DS.Palette.warning)
                }
                Button("Dismiss", action: onDismiss)
                    .buttonStyle(.borderless)
            }
            .controlSize(.small)
            .disabled(busy)
            .padding(.top, 4)
            if busy {
                Text("Working…")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DS.Palette.warning.opacity(0.08), in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
    }
}

/// A 202'd approval or re-send waiting for the broker (`_UncertainCardView`).
private struct SwingUncertainCardView: View {
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
            SwingCardHeader(signal: card.signal, showScore: false)
            AppBadge(label: card.badge, color: tone)
            Text(swingSessionText(card.signal))
                .font(.caption2)
                .foregroundStyle(.tertiary)
            Text(swingUncertainCopy(card))
                .font(.caption)
                .fixedSize(horizontal: false, vertical: true)
            if canDismiss {
                Button("Dismiss", action: onDismiss)
                    .buttonStyle(.borderless)
                    .controlSize(.small)
                    .padding(.top, 4)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(tone.opacity(0.08), in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
    }
}

/// The wheel lane's open cash-secured puts and latest scans (`WheelCard`).
/// Read-only: a red ITM figure means the 15:45 ET monitor will buy that put
/// back on its next pass.
struct WheelCard: View {
    let model: WheelModel

    var body: some View {
        Card(padding: 16) {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text("Wheel")
                        .font(.subheadline.weight(.semibold))
                        .accessibilityAddTraits(.isHeader)
                    Spacer()
                    if let at = model.state.value?.fetchedAt {
                        Text(fmtAsOf(at))
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                }
                switch model.state {
                case .loading:
                    Text("Loading…")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                case .failed(let error):
                    ErrorRow(message: wheelErrorMessage(error)) {
                        Task { await model.retry() }
                    }
                case .loaded(let w):
                    WheelBody(wheel: w)
                }
            }
        }
        .task { if model.state.value == nil { await model.load() } }
    }
}

private struct WheelBody: View {
    let wheel: WheelSnapshot

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                WheelStat(label: "OPEN PUTS", value: "\(wheel.openPuts.count)")
                WheelStat(label: "COLLATERAL", value: fmtMoney(wheel.collateralTotal))
                WheelStat(label: "CASH", value: fmtMoney(wheel.cash))
            }
            if wheel.openPuts.isEmpty {
                Text("No open puts.")
                    .font(.footnote)
                    .italic()
                    .foregroundStyle(.secondary)
            } else {
                ForEach(Array(wheel.openPuts.enumerated()), id: \.offset) { _, put in
                    WheelPutRow(put: put)
                }
            }
            Text("RECENT SCANS")
                .font(.caption2)
                .tracking(0.8)
                .foregroundStyle(.secondary)
            if wheel.recentScans.isEmpty {
                Text("No scans recorded yet.")
                    .font(.footnote)
                    .italic()
                    .foregroundStyle(.secondary)
            } else {
                ForEach(Array(wheel.recentScans.prefix(5).enumerated()), id: \.offset) { _, scan in
                    WheelScanRow(scan: scan)
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
        VStack(alignment: .leading, spacing: 4) {
            Text("\(put.underlying) \(fmtMoney(put.strike)) P · \(put.expiry)")
                .font(.subheadline.weight(.semibold))
            Text(put.contract)
                .font(.caption2.monospaced())
                .foregroundStyle(.tertiary)
            HStack(alignment: .top) {
                WheelStat(label: "QTY", value: put.qty.map(String.init) ?? "—")
                WheelStat(label: "ENTRY", value: fmtMoney(put.avgEntryPrice))
                WheelStat(label: "MARK", value: fmtMoney(put.currentPrice))
            }
            .padding(.top, 2)
            HStack(alignment: .top) {
                WheelStat(label: "ITM", value: fmtItm(put.itmPct), color: itmColor)
                WheelStat(label: "DTE", value: put.dte.map(String.init) ?? "—")
                WheelStat(
                    label: "P&L",
                    value: fmtPnl(put.unrealizedPl),
                    color: put.unrealizedPl == nil ? .secondary : pnlColor(put.unrealizedPl)
                )
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
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
        HStack(alignment: .top, spacing: 8) {
            VStack(alignment: .leading, spacing: 1) {
                Text("\(scan.symbol) \(fmtMoney(scan.strike)) P · \(scan.expiry.isEmpty ? "—" : scan.expiry)")
                    .font(.caption)
                if !scan.skipReason.isEmpty {
                    Text(scan.skipReason)
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            AppBadge(label: scan.status.isEmpty ? "—" : scan.status, color: statusColor)
        }
    }
}

private struct WheelStat: View {
    let label: String
    let value: String
    var color: Color = .primary

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption2)
                .tracking(0.4)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.caption.weight(.bold).monospacedDigit())
                .foregroundStyle(color)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}
