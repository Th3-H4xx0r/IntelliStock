import Observation
import SwiftUI

/// The confirmation and in-flight state behind the pending-signal actions.
/// The instance screen owns it and presents its confirmation, so the
/// alert lives on the screen, not on a list row that can scroll away.
@Observable
final class PendingSignalsActions {
    var confirm: ConfirmRequest?
    /// An approval's order review (Approve / Approve ½), shown as a sheet.
    var review: SwingOrderReview?
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
    /// The account's cash (the wheel book's), for the collateral check.
    var cash: Double?
    /// Shows a result (Dart's SnackBar) on the screen's toast.
    let showToast: (Toast) -> Void
    /// After a confirmed decision lands (the scan rows re-read their links).
    var onDecided: () -> Void = {}

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
                    SwingQuietRow(title: "Nothing waiting for review.", systemImage: "checkmark.circle")
                }
            } header: {
                header(title)
            } footer: {
                if state.signals.isEmpty, !hasTail { Text(Self.footer) }
            }
        }
        ForEach(Array(state.signals.enumerated()), id: \.element.id) { i, s in
            Section {
                SwingSignalRows(
                    signal: s,
                    busy: state.isDeciding(s.id) || actions.inFlight.contains(s.id) || actions.confirmRunning,
                    sending: state.isDeciding(s.id) || actions.inFlight.contains(s.id),
                    refusal: state.refusals[s.id],
                    cash: cash,
                    onDecide: { decide(s, $0) }
                )
            } header: {
                if i == 0, !hasStatus { header(title) }
            } footer: {
                if i == state.signals.count - 1, !hasTail, state.tracks.isEmpty { Text(Self.footer) }
            }
        }
        // Approvals followed through their broker command.
        if !state.tracks.isEmpty {
            Section {
                ForEach(state.tracks) { track in
                    SwingTrackRow(track: track) { model.dismissTrack(track.id) }
                }
            } header: {
                Text("Your approvals")
            } footer: {
                if !hasTail { Text(Self.footer) }
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

    /// The section title, with a button that rehearses the order review on
    /// a sample order (nothing is sent).
    private func header(_ title: String) -> some View {
        HStack {
            Text(title)
            Spacer()
            Button("Demo") { actions.review = .demo() }
                .font(.footnote.weight(.semibold))
                .textCase(nil)
                .accessibilityHint("Shows the order review with a sample order. Nothing is sent.")
        }
    }

    // MARK: Actions

    /// The decision confirm, then the request; the result shows as a toast
    /// unless it was ignored or uncertain (that advice lives on its card).
    private func decide(_ signal: SwingSignal, _ decision: String) {
        let actions = actions
        let model = model
        let showToast = showToast
        let onDecided = onDecided
        if decision != "reject" {
            // Approvals go through the order review and its swipe to send.
            actions.review = SwingOrderReview(
                signal: signal,
                decision: decision,
                send: {
                    actions.inFlight.insert(signal.id)
                    actions.confirmRunning = true
                    defer {
                        actions.inFlight.remove(signal.id)
                        actions.confirmRunning = false
                    }
                    return await model.decide(signal, decision)
                },
                finished: { _ in onDecided() }
            )
            return
        }
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
                onDecided()
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
func swingFieldLabel(_ label: String) -> String {
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
    /// This card's own decision is in flight.
    let sending: Bool
    /// Why the broker refused its last approval, when this device saw it.
    let refusal: SwingRefusal?
    let cash: Double?
    let onDecide: (String) -> Void

    var body: some View {
        let s = signal
        let reasoning = s.reasoning.trimmingCharacters(in: .whitespacesAndNewlines)
        // Header: symbol, lane, score capsule, session.
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(s.symbol)
                    .font(.title2.weight(.bold))
                Text(swingLaneLabel(s))
                    .dsBadge(.secondary)
                Spacer(minLength: 0)
                StatusBadge(label: swingScoreLabel(s.score), color: swingScoreColor(s.score))
            }
            Text(swingSessionText(s).dsSentenceCased)
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 2)
        .id(swingSignalAnchor(s.id))
        .accessibilityElement(children: .combine)

        // Key numbers: premium and collateral lead for a put seller.
        if s.isWheel {
            let lead = swingWheelLead(s)
            StatGrid(columns: 2) {
                StatCell(label: "Premium", footnote: lead.premiumFootnote) {
                    Text(lead.premium).font(.title3.weight(.semibold)).foregroundStyle(DS.Palette.success)
                }
                StatCell(label: "Collateral", footnote: lead.collateralFootnote) {
                    Text(lead.collateral).font(.title3.weight(.semibold))
                }
            }
            .padding(.vertical, 4)
            StatGrid(columns: 3) {
                ForEach(Array(swingWheelDetails(s).enumerated()), id: \.offset) { _, field in
                    StatCell(label: field.label, value: field.value)
                }
                if let otm = s.otmPct {
                    StatCell(label: "Cushion", value: fmtItm(-otm))
                }
            }
            .padding(.vertical, 4)
        } else {
            StatGrid(columns: 2) {
                ForEach(Array(swingProposalFields(s).enumerated()), id: \.offset) { _, field in
                    StatCell(label: swingFieldLabel(field.label), value: field.value)
                }
            }
            .padding(.vertical, 4)
        }
        if let warning = swingCollateralWarning(s, cash: cash) {
            SwingCallout(text: warning, systemImage: "banknote", color: DS.Palette.warning)
        }
        if !s.keyRisksText.isEmpty {
            SwingCallout(text: s.keyRisks.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }.joined(separator: "\n"),
                         systemImage: "exclamationmark.triangle.fill", color: DS.Palette.warning)
        }
        if !reasoning.isEmpty {
            DisclosureGroup("Rationale") {
                Text(reasoning)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        // The outcome of the last approval, when it was refused.
        if let refusal {
            let text = swingRefusalText(refusal.message)
            SwingCallout(title: "Not sent", text: text.headline, detail: text.detail,
                         systemImage: "xmark.octagon.fill", color: DS.Palette.danger)
        } else if let note = swingReturnedNote(s) {
            SwingCallout(title: "Returned by the broker", text: note,
                         systemImage: "arrow.uturn.backward.circle.fill", color: DS.Palette.danger)
        }
        VStack(alignment: .leading, spacing: 10) {
            Button { onDecide("approve") } label: {
                Text(refusal == nil && !s.returnedByBroker ? decisionLabel("approve") : "Approve Again")
                    .font(.headline)
                    .frame(maxWidth: .infinity, minHeight: 30)
            }
            .dsProminentButton()
            HStack(spacing: 10) {
                if s.allowsHalf {
                    Button { onDecide("approve_half") } label: {
                        Text(decisionLabel("approve_half")).frame(maxWidth: .infinity, minHeight: 30)
                    }
                    .tint(DS.Palette.success)
                }
                Button(role: .destructive) { onDecide("reject") } label: {
                    Text(decisionLabel("reject")).frame(maxWidth: .infinity, minHeight: 30)
                }
                .tint(DS.Palette.danger)
            }
            .buttonStyle(.bordered)
            if sending {
                HStack(spacing: 8) {
                    ProgressView()
                    Text("Sending your decision…")
                }
                .font(.footnote)
                .foregroundStyle(.secondary)
            } else if busy {
                Text("Working…")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .controlSize(.large)
        .disabled(busy)
        .padding(.vertical, 6)
    }
}

/// A flat tinted row: risks, a refusal, a collateral warning.
private struct SwingCallout: View {
    var title: String?
    let text: String
    var detail: String?
    let systemImage: String
    let color: Color

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: systemImage)
                .font(.body)
                .foregroundStyle(color)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                if let title {
                    Text(title).font(.subheadline.weight(.semibold))
                }
                Text(text)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                if let detail, !detail.isEmpty, detail != text {
                    Text(detail)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 4)
        .listRowBackground(color.opacity(DS.tintFill))
        .accessibilityElement(children: .combine)
    }
}

/// An approval followed through its broker command.
private struct SwingTrackRow: View {
    let track: SwingApprovalTrack
    let onDismiss: () -> Void

    var body: some View {
        let s = track.signal
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text(s.symbol).font(.headline)
                Text(swingLaneLabel(s)).dsBadge(.secondary)
                Spacer(minLength: 0)
                switch track.phase {
                case .sending:
                    ProgressView()
                case .sent:
                    StatusBadge(label: "Sent", color: DS.Palette.success)
                case .refused:
                    StatusBadge(label: "Not sent", color: DS.Palette.danger)
                }
            }
            switch track.phase {
            case .sending:
                Text("Approved. Waiting for the broker to send it…")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            case .sent:
                Text("The broker sent the order. Check open orders for the fill.")
                    .font(.subheadline)
            case .refused:
                let text = swingRefusalText(track.message)
                Text(text.headline).font(.subheadline)
                if text.detail != text.headline, !text.detail.isEmpty {
                    Text(text.detail).font(.footnote).foregroundStyle(.secondary)
                }
            }
            if track.phase != .sending {
                Button("Dismiss", action: onDismiss)
                    .buttonStyle(.borderless)
                    .padding(.top, 2)
            }
        }
        .padding(.vertical, 4)
    }
}

/// The scroll anchor on a signal card's header.
func swingSignalAnchor(_ id: String) -> String { "swing-signal-\(id)" }

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
    /// The ids in the live pending queue.
    var pendingIds: Set<String> = []
    /// Scrolls to a pending signal's card.
    var onReview: (String) -> Void = { _ in }

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
                    SwingQuietRow(title: "No open puts.", systemImage: "shield")
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
            Section {
                if w.recentScans.isEmpty {
                    SwingQuietRow(title: "No scans recorded yet.", systemImage: "magnifyingglass")
                } else {
                    ForEach(Array(w.recentScans.prefix(5).enumerated()), id: \.offset) { _, scan in
                        let signal = wheelScanSignal(scan, in: model.signals)
                        let status = wheelScanStatus(scan, signal: signal, pendingIds: pendingIds)
                        if status.reviewable, let signal {
                            Button { onReview(signal.id) } label: {
                                WheelScanRow(scan: scan, status: status, showsChevron: true)
                            }
                            .buttonStyle(.plain)
                            .accessibilityHint("Shows its pending signal, where you approve or reject it")
                        } else {
                            WheelScanRow(scan: scan, status: status, showsChevron: false)
                        }
                    }
                }
            } header: {
                Text("Recent scans")
            } footer: {
                if !w.recentScans.isEmpty {
                    Text("A scan is a log entry. Approve or reject a put on its pending signal above; tap a pending scan to jump to it.")
                }
            }
        }
    }
}

/// A quiet in-section empty state, in the manner of `ContentUnavailableView`.
private struct SwingQuietRow: View {
    let title: String
    let systemImage: String

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: systemImage)
                .foregroundStyle(.tertiary)
                .accessibilityHidden(true)
            Text(title)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, minHeight: 44)
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
    let status: WheelScanStatus
    let showsChevron: Bool

    private var statusColor: Color {
        switch status.tone {
        case .good: DS.Palette.success
        case .waiting: DS.Palette.warning
        case .bad: DS.Palette.danger
        case .neutral: .secondary
        }
    }

    private var subtitle: String {
        var parts: [String] = []
        if let score = scan.score { parts.append("Score \(score)") }
        if !scan.session.isEmpty { parts.append("Scanned \(scan.session)") }
        if !scan.skipReason.isEmpty { parts.append(scan.skipReason) }
        return parts.joined(separator: " · ")
    }

    var body: some View {
        HStack(spacing: 8) {
            EntityRow(
                "\(scan.symbol) \(fmtMoney(scan.strike)) P · \(scan.expiry.isEmpty ? "—" : scan.expiry)",
                subtitle: subtitle.isEmpty ? nil : subtitle,
                subtitleLineLimit: 2
            ) {
                StatusBadge(label: status.label, color: statusColor)
            }
            if showsChevron {
                Image(systemName: "chevron.forward")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.tertiary)
                    .accessibilityHidden(true)
            }
        }
        .contentShape(Rectangle())
        .frame(minHeight: 44)
    }
}
