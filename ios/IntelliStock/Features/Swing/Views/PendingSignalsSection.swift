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

/// AI-scored swing and wheel candidates that wait for a human — the web's
/// `PendingSignalsCard` on the instance detail screen — as one `DSSection`
/// card: each signal a row (symbol, lane, score and session; the proposal's
/// figures; the reasoning and risks; Approve, Approve ½ and Reject), then
/// "Your approvals", "Waiting for the broker" and "Approved, not yet sent"
/// under their own sub-headers, and the footer under the card.
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

    static let title = "Pending AI signals"
    static let footer = "Approval rebuilds the order at the live price: shares, stop and target for swing, strike and expiry for the wheel."

    var body: some View {
        let count = model.state.value?.signals.count
        let subtitle = count.map { "\($0) scored \($0 == 1 ? "candidate" : "candidates") waiting for your review" }
            ?? "Scored candidates waiting for your review"
        switch model.state {
        case .loading:
            DSSection(Self.title, subtitle: subtitle) {
                VStack(alignment: .leading, spacing: 12) {
                    Skeleton.line(width: 160, height: 14)
                    Skeleton.line(width: 240, height: 14)
                }
                .padding(.vertical, 8)
                .accessibilityLabel("Loading")
            } accessory: {
                demoButton
            }
        case .failed:
            DSSection(Self.title, subtitle: subtitle, style: .plain) {
                ErrorRow(message: model.state.errorMessage ?? "") {
                    Task { await model.build() }
                }
                .dsClearRow()
            } accessory: {
                demoButton
            }
        case .loaded(let state):
            loaded(state, subtitle: subtitle)
        }
    }

    private func loaded(_ state: PendingSignalsState, subtitle: String) -> some View {
        let now = state.asOf ?? Date()
        return DSSection(Self.title, subtitle: subtitle, rowMinHeight: 40) {
            if let error = state.refreshError {
                SwingNoteRow(text: "Last refresh failed: \(error)")
            }
            if state.signals.isEmpty {
                EmptyState(
                    title: "Nothing waiting for review",
                    subtitle: "Scores from the review threshold up to the auto-approve threshold wait here"
                )
            }
            ForEach(state.signals, id: \.id) { s in
                SwingSignalRows(
                    signal: s,
                    busy: state.isDeciding(s.id) || actions.inFlight.contains(s.id) || actions.confirmRunning,
                    sending: state.isDeciding(s.id) || actions.inFlight.contains(s.id),
                    refusal: state.refusals[s.id],
                    cash: cash,
                    onDecide: { decide(s, $0) }
                )
            }
            // Approvals followed through their broker command.
            if !state.tracks.isEmpty {
                SwingSubheaderRow(title: "Your approvals", count: state.tracks.count, warn: false)
                ForEach(state.tracks) { track in
                    SwingTrackRow(track: track) { model.dismissTrack(track.id) }
                }
            }
            // 202'd approvals and re-sends: the card and its badge stay until a
            // poll settles them.
            if !state.uncertain.isEmpty {
                SwingSubheaderRow(title: "Waiting for the broker", count: state.uncertain.count, warn: true)
                ForEach(state.uncertain, id: \.signal.id) { card in
                    SwingUncertainRow(
                        card: card,
                        canDismiss: card.canDismiss(now),
                        onDismiss: { model.dismissUncertain(card.signal.id) }
                    )
                }
            }
            // Approvals no broker command has claimed for 2+ minutes.
            if !state.stuck.isEmpty {
                SwingSubheaderRow(title: "Approved, not yet sent", count: state.stuck.count, warn: true)
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
            }
        } footer: {
            Text(Self.footer)
        } accessory: {
            demoButton
        }
    }

    /// Rehearses the order review on a sample order (nothing is sent).
    private var demoButton: some View {
        Button("Demo") { actions.review = .demo() }
            .buttonStyle(.dsGhost)
            .padding(.trailing, -12)
            .accessibilityHint("Shows the order review with a sample order. Nothing is sent.")
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

/// "11/12/2026, 12:23:43 PM" — the web's `toLocaleString` of a signal's
/// `created_at`; nil when the timestamp does not parse.
private func swingCreatedText(_ raw: String) -> String? {
    let fractional = Date.ISO8601FormatStyle(includingFractionalSeconds: true)
    let whole = Date.ISO8601FormatStyle()
    guard let date = (try? fractional.parse(raw)) ?? (try? whole.parse(raw)) else { return nil }
    return date.formatted(date: .numeric, time: .standard)
}

/// A card's head, as the web's: the symbol (14/500), the lane and a status
/// word (the score, "Approved") in 12 pt, then the session line under them.
private struct SwingSignalHead: View {
    let signal: SwingSignal
    let status: String
    var statusColor: Color = DS.Text.secondary
    let session: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text(signal.symbol)
                    .font(DS.Font.bodyMedium)
                    .foregroundStyle(DS.Text.primary)
                Text(swingLaneLabel(signal))
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.secondary)
                Text(status)
                    .font(DS.Font.caption)
                    .foregroundStyle(statusColor)
                    .monospacedDigit()
                    .lineLimit(1)
            }
            Text(session)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .monospacedDigit()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

/// One signal awaiting a decision (`_SignalCard`), as one row of the card:
/// the head, the proposal's figures, any collateral warning, the reasoning
/// (long reasoning collapsed behind Show more) and risks, the last refusal,
/// then Approve, Approve ½ and Reject.
private struct SwingSignalRows: View {
    let signal: SwingSignal
    let busy: Bool
    /// This card's own decision is in flight.
    let sending: Bool
    /// Why the broker refused its last approval, when this device saw it.
    let refusal: SwingRefusal?
    let cash: Double?
    let onDecide: (String) -> Void

    @State private var expanded = false

    var body: some View {
        let s = signal
        let reasoning = s.reasoning.trimmingCharacters(in: .whitespacesAndNewlines)
        let risks = s.keyRisks.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
        VStack(alignment: .leading, spacing: 12) {
            SwingSignalHead(
                signal: s,
                status: swingScoreLabel(s.score),
                statusColor: swingScoreColor(s.score),
                session: sessionLine(s)
            )

            StatGrid(columns: 2, horizontalSpacing: 16, verticalSpacing: 12) {
                ForEach(Array(fields(s).enumerated()), id: \.offset) { _, field in
                    StatCell(label: field.label, footnote: field.foot) {
                        Text(field.value)
                            .font(field.mono ? DS.Font.mono : DS.Font.body)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }
                }
            }

            if let warning = swingCollateralWarning(s, cash: cash) {
                SwingNoteRow(text: warning)
            }

            if !reasoning.isEmpty {
                reasoningView(reasoning)
            }

            if !risks.isEmpty {
                Text("\(Text("Risks").foregroundStyle(DS.Text.muted)) \(risks.joined(separator: " · "))")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // The outcome of the last approval, when it was refused.
            if let refusal {
                let text = swingRefusalText(refusal.message)
                SwingErrorCallout(title: "Not sent", text: text.headline, detail: text.detail)
            } else if let note = swingReturnedNote(s) {
                SwingWarnCallout(title: "Returned by the broker", text: note)
            }

            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    Button(refusal == nil && !s.returnedByBroker ? decisionLabel("approve") : "Approve again") {
                        onDecide("approve")
                    }
                    if s.allowsHalf {
                        Button(decisionLabel("approve_half")) { onDecide("approve_half") }
                    }
                    Button(decisionLabel("reject"), role: .destructive) { onDecide("reject") }
                }
                .buttonStyle(.dsSecondary(size: .medium))
                if sending {
                    HStack(spacing: 8) {
                        ProgressView()
                            .controlSize(.small)
                        Text("Sending your decision…")
                    }
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                } else if busy {
                    Text("Working…")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                }
            }
            .disabled(busy)
        }
        .padding(.vertical, 8)
        .id(swingSignalAnchor(s.id))
    }

    /// "Session 2026-11-12 · 11/12/2026, 12:23:43 PM".
    private func sessionLine(_ s: SwingSignal) -> String {
        let session = swingSessionText(s).dsSentenceCased
        guard let created = swingCreatedText(s.createdAt) else { return session }
        return "\(session) · \(created)"
    }

    /// The web's proposal rows. A put adds the collateral's return under it
    /// and the cushion to the strike; the broker resolves the contract and
    /// the limit when you approve, which the rows say.
    private func fields(_ s: SwingSignal) -> [(label: String, value: String, foot: String?, mono: Bool)] {
        guard s.isWheel else {
            return swingProposalFields(s).map { (swingFieldLabel($0.label), $0.value, nil, false) }
        }
        let credit = s.creditEst
        var rows: [(label: String, value: String, foot: String?, mono: Bool)] = [
            ("Contract", s.contract.isEmpty ? "Picked at approval" : s.contract, nil, !s.contract.isEmpty),
            ("Strike", fmtMoney(s.strike), nil, false),
            ("Expiry", s.expiry.isEmpty ? "—" : s.expiry, nil, false),
            ("Qty", s.qty.map(String.init) ?? "—", nil, false),
            ("Limit", s.limitPrice == nil ? "Set at approval" : fmtMoney(s.limitPrice), nil, false),
            ("Premium", credit == nil ? fmtMoney(s.premiumEst) : "\(fmtMoney(s.premiumEst)) (\(fmtMoney(credit)))", nil, false),
            ("Collateral", fmtMoney(s.collateral), swingWheelLead(s).collateralFootnote, false),
        ]
        if let otm = s.otmPct { rows.append(("Cushion", fmtItm(-otm), nil, false)) }
        return rows
    }

    @ViewBuilder
    private func reasoningView(_ text: String) -> some View {
        let preview = swingReasoningPreview(text)
        VStack(alignment: .leading, spacing: 4) {
            Text(expanded || !preview.truncated ? text : preview.text)
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.label)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
            if preview.truncated {
                Button(expanded ? "Show less" : "Show more") {
                    withAnimation(DSMotion.standard) { expanded.toggle() }
                }
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.secondary)
                .buttonStyle(.plain)
            }
        }
    }
}

/// A warning line inside the card: an amber dot and the words (a stale
/// refresh, collateral above the account's cash).
private struct SwingNoteRow: View {
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            DSStatusDot(.warn)
                .padding(.top, 2)
            Text(text)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.label)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

/// A broker refusal on the card (the web's `ui-error`): the title and the
/// plain headline in loss, the server's own words under them.
private struct SwingErrorCallout: View {
    let title: String
    let text: String
    var detail: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(DS.Font.bodyMedium)
                .foregroundStyle(DS.Palette.loss)
            Text(text)
                .font(DS.Font.body)
                .foregroundStyle(DS.Palette.loss)
                .fixedSize(horizontal: false, vertical: true)
            if let detail, !detail.isEmpty, detail != text {
                Text(detail)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsErrorSurface(radius: DS.Radius.control)
        .accessibilityElement(children: .combine)
    }
}

/// A note that needs a look but is not an error (the broker put an approval
/// back): an amber dot and the title over the words, on the raised surface.
private struct SwingWarnCallout: View {
    let title: String
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                DSStatusDot(.warn)
                Text(title)
                    .font(DS.Font.bodyMedium)
                    .foregroundStyle(DS.Text.primary)
            }
            Text(text)
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.label)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsCardSurface(fill: DS.Surface.raised, radius: DS.Radius.control)
        .accessibilityElement(children: .combine)
    }
}

/// A group's sub-header inside the card (the web's 40 pt rows): an amber
/// dot when the group needs attention, its name, then its count.
private struct SwingSubheaderRow: View {
    let title: String
    let count: Int
    let warn: Bool

    var body: some View {
        HStack(spacing: 8) {
            if warn { DSStatusDot(.warn) }
            Text(title)
                .foregroundStyle(DS.Text.secondary)
            Text("\(count)")
                .foregroundStyle(DS.Text.muted)
                .monospacedDigit()
        }
        .font(DS.Font.caption)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }
}

/// An approval followed through its broker command.
private struct SwingTrackRow: View {
    let track: SwingApprovalTrack
    let onDismiss: () -> Void

    var body: some View {
        let s = track.signal
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text(s.symbol)
                    .font(DS.Font.bodyMedium)
                    .foregroundStyle(DS.Text.primary)
                Text(swingLaneLabel(s))
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.secondary)
                Spacer(minLength: 0)
                switch track.phase {
                case .sending:
                    ProgressView()
                        .controlSize(.small)
                case .sent:
                    StatusDot("Sent", tone: .positive, font: DS.Font.caption)
                case .refused:
                    StatusDot("Not sent", tone: .error, font: DS.Font.caption)
                }
            }
            switch track.phase {
            case .sending:
                Text("Approved. Waiting for the broker to send it…")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.secondary)
            case .sent:
                Text("The broker sent the order. Check open orders for the fill.")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.label)
            case .refused:
                let text = swingRefusalText(track.message)
                Text(text.headline)
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Palette.loss)
                if text.detail != text.headline, !text.detail.isEmpty {
                    Text(text.detail)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                }
            }
            if track.phase != .sending {
                Button("Dismiss", action: onDismiss)
                    .buttonStyle(.dsGhost)
                    .padding(.leading, -12)
            }
        }
        .padding(.vertical, 8)
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
        VStack(alignment: .leading, spacing: 8) {
            SwingSignalHead(
                signal: s,
                status: s.status == "approved_half" ? "Approved ½" : "Approved",
                session: swingSessionText(s).dsSentenceCased
            )
            Text(label)
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.label)
            if let blockedReason {
                Text(blockedReason)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
            }
            HStack(spacing: 8) {
                if blockedReason == nil {
                    Button("Re-send", action: onResend)
                        .buttonStyle(.dsSecondary(size: .medium))
                }
                Button("Dismiss", action: onDismiss)
                    .buttonStyle(.dsGhost(size: .medium))
            }
            .disabled(busy)
            if busy {
                Text("Working…")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
            }
        }
        .padding(.vertical, 8)
    }
}

/// A 202'd approval or re-send waiting for the broker (`_UncertainCardView`).
private struct SwingUncertainRow: View {
    let card: UncertainCard
    let canDismiss: Bool
    let onDismiss: () -> Void

    /// The web's `uncertainBadgeClass`: a small warning word until a poll
    /// settles it, then gain or loss.
    private var tone: Color {
        switch card.resolved {
        case "submitted": DS.Palette.gain
        case "failed": DS.Palette.loss
        default: DS.Palette.amber
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            SwingSignalHead(
                signal: card.signal,
                status: card.badge.dsSentenceCased,
                statusColor: tone,
                session: swingSessionText(card.signal).dsSentenceCased
            )
            Text(swingUncertainCopy(card))
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.soft)
                .fixedSize(horizontal: false, vertical: true)
            if canDismiss {
                Button("Dismiss", action: onDismiss)
                    .buttonStyle(.dsGhost)
                    .padding(.leading, -12)
            }
        }
        .padding(.vertical, 8)
    }
}

/// The wheel lane's book and latest scans (the web's `WheelPanel`), as two
/// sections. The Wheel card leads with open puts, collateral and cash, then
/// each put as a row in the manner of the Dashboard's holdings that opens
/// the option's page; "Buyback due" means the 15:45 ET monitor will buy that
/// put back on its next pass. The scans show the latest three, the rest
/// behind Show all. The screen runs the load.
struct WheelSections: View {
    let model: WheelModel
    /// The ids in the live pending queue.
    var pendingIds: Set<String> = []
    /// The account the puts are in, for the option's page.
    var brokerageId: String?
    /// The account's equity, for the option page's share of the portfolio.
    var portfolioTotal: Double?
    /// Scrolls to a pending signal's card.
    var onReview: (String) -> Void = { _ in }

    /// Scans shown before Show all.
    static let scanPreview = 3

    @State private var showAllScans = false

    var body: some View {
        DSSection("Wheel", subtitle: subtitle) {
            switch model.state {
            case .loading:
                VStack(alignment: .leading, spacing: 12) {
                    Skeleton.line(width: 200, height: 14)
                    Skeleton.line(width: 140, height: 14)
                }
                .padding(.vertical, 8)
                .accessibilityLabel("Loading")
            case .failed(let error):
                HStack(spacing: 12) {
                    Text(wheelErrorMessage(error))
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Palette.loss)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .fixedSize(horizontal: false, vertical: true)
                    Button("Retry") { Task { await model.retry() } }
                        .buttonStyle(.dsSecondary)
                }
            case .loaded(let w):
                StatGrid(columns: 3, horizontalSpacing: 12) {
                    StatCell(label: "Open puts", value: "\(w.openPuts.count)")
                    StatCell(label: "Collateral", value: fmtMoney(w.collateralTotal))
                    StatCell(label: "Cash", value: fmtMoney(w.cash))
                }
                .statValueStyle(.medium)
                .padding(.vertical, 8)
                if w.openPuts.isEmpty {
                    EmptyState(title: "No open puts")
                } else {
                    ForEach(Array(w.openPuts.enumerated()), id: \.offset) { _, put in
                        putRow(put)
                    }
                }
            }
        }
        if let w = model.state.value {
            DSSection("Recent scans") {
                if w.recentScans.isEmpty {
                    EmptyState(title: "No scans recorded yet")
                } else {
                    let shown = showAllScans ? w.recentScans : Array(w.recentScans.prefix(Self.scanPreview))
                    ForEach(Array(shown.enumerated()), id: \.offset) { _, scan in
                        scanRow(scan)
                    }
                    if w.recentScans.count > Self.scanPreview {
                        Button {
                            withAnimation(DSMotion.standard) { showAllScans.toggle() }
                        } label: {
                            Text(showAllScans ? "Show less" : "Show all (\(w.recentScans.count))")
                                .font(DS.Font.body)
                                .foregroundStyle(DS.Text.secondary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
            } footer: {
                if !w.recentScans.isEmpty {
                    Text("A scan is a log entry. Approve or reject a put on its pending signal above; tap a pending scan to jump to it.")
                }
            }
        }
    }

    /// "2 open · as of 13:10" once the book has loaded.
    private var subtitle: String? {
        guard let w = model.state.value else { return nil }
        let open = "\(w.openPuts.count) open"
        let asOf = fmtAsOf(w.fetchedAt)
        return asOf.isEmpty ? open : "\(open) · \(asOf)"
    }

    /// A put that opens its option page with the position, as a holding does.
    @ViewBuilder
    private func putRow(_ put: WheelPut) -> some View {
        if let position = wheelPutPosition(put) {
            NavigationLink(value: Route.stock(StockRoute(
                symbol: put.contract,
                position: position,
                brokerageId: brokerageId,
                portfolioTotal: portfolioTotal
            ))) {
                WheelPutRow(put: put)
            }
            .accessibilityHint("Opens the option")
            .contextMenu {
                Button("Copy contract", systemImage: "doc.on.doc") { UIPasteboard.general.string = put.contract }
            }
        } else {
            WheelPutRow(put: put)
        }
    }

    @ViewBuilder
    private func scanRow(_ scan: WheelScan) -> some View {
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

/// One open put, as a holding row: "QCOM $177.50 Put" over "7 days left ·
/// 4.1% above strike", the P&L on the right over the contract count, or
/// "● Buyback due" when the monitor will close it. The OCC code is read to
/// VoiceOver only.
private struct WheelPutRow: View {
    let put: WheelPut

    var body: some View {
        let pl = put.unrealizedPl
        EntityRow(wheelPutTitle(put), subtitle: wheelPutSubtitle(put)) {
            VStack(alignment: .trailing, spacing: 2) {
                Text(fmtPnl(pl))
                    .font(DS.Font.body)
                    .foregroundStyle(pl == nil ? DS.Text.muted : pnlColor(pl))
                if put.monitorWillBuyBack {
                    StatusDot("Buyback due", tone: .warn, font: DS.Font.caption)
                } else if let qty = put.qty {
                    Text(qty == 1 ? "1 contract" : "\(qty) contracts")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                }
            }
            .monospacedDigit()
            .lineLimit(1)
        }
        .entityRowTitleMedium()
        .accessibilityValue("Contract \(put.contract)")
    }
}

/// One scan: "XLE $88.00 P · 2026-11-20" over its score, session and skip
/// reason, its status as a dot and a word, and a chevron when it jumps to
/// its pending signal.
private struct WheelScanRow: View {
    let scan: WheelScan
    let status: WheelScanStatus
    let showsChevron: Bool

    /// The web's scan tones: placed or approved a zinc-300 dot, pending
    /// amber, rejected or failed loss, the rest stopped grey.
    private var tone: DSStatusTone {
        switch status.tone {
        case .good: .neutral
        case .waiting: .warn
        case .bad: .error
        case .neutral: .off
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
                StatusDot(status.label, tone: tone, font: DS.Font.caption)
            }
            if showsChevron {
                Image(systemName: "chevron.forward")
                    .font(.system(size: 13, weight: .regular))
                    .foregroundStyle(DS.Text.faint)
                    .accessibilityHidden(true)
            }
        }
        .contentShape(Rectangle())
    }
}
