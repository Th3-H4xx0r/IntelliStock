import SwiftUI

/// An approval waiting on its order review: the screen presents it as a
/// sheet (`PendingSignalsActions.review`).
struct SwingOrderReview: Identifiable {
    let id = UUID()
    let signal: SwingSignal
    /// "approve" or "approve_half".
    let decision: String
    /// Sends the decision (a REAL trading action).
    let send: () async -> DecisionResult
    /// After the sheet closes on a result.
    let finished: (DecisionResult) -> Void
}

/// The order review before an approval is sent, in the manner of a
/// brokerage's "swipe up to submit": the order's figures, the broker's
/// caveat verbatim, then a swipe up to send. A recorded approval plays the
/// sent mark and closes; a failure stays open with its reason.
struct SwingOrderReviewSheet: View {
    let review: SwingOrderReview
    let cash: Double?

    @Environment(\.dismiss) private var dismiss
    @State private var phase: Phase = .review

    enum Phase: Equatable {
        case review
        case sending
        case done(DecisionResult)
    }

    var body: some View {
        NavigationStack {
            Group {
                switch phase {
                case .review, .sending:
                    reviewContent
                case .done(let result):
                    SwingOrderOutcomeView(result: result)
                }
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    if phase == .review || phase == .sending {
                        Button("Cancel") { dismiss() }
                            .disabled(phase == .sending)
                    }
                }
                ToolbarItem(placement: .confirmationAction) {
                    if case .done(let result) = phase, !Self.isSent(result) {
                        Button("Done") { close(result) }
                    }
                }
            }
        }
        .interactiveDismissDisabled(phase != .review)
        .sensoryFeedback(trigger: phase) { _, new in
            guard case .done(let result) = new else { return nil }
            return Self.isSent(result) ? .success : .error
        }
    }

    /// The approval reached the server: the broker takes it from here.
    nonisolated static func isSent(_ result: DecisionResult) -> Bool {
        result.outcome == .recorded || result.outcome == .uncertain
    }

    // MARK: Review

    private var reviewContent: some View {
        let s = review.signal
        return VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(review.decision == "approve_half" ? "Review order · half size" : "Review order")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.secondary)
                        Text(s.symbol)
                            .font(.largeTitle.weight(.bold))
                        Text(swingLaneLabel(s))
                            .font(.body)
                            .foregroundStyle(.secondary)
                    }
                    .accessibilityElement(children: .combine)

                    VStack(spacing: 0) {
                        ForEach(Array(rows(s).enumerated()), id: \.offset) { i, row in
                            if i > 0 { Divider() }
                            HStack(alignment: .firstTextBaseline) {
                                Text(row.label)
                                    .foregroundStyle(.secondary)
                                Spacer(minLength: 12)
                                Text(row.value)
                                    .fontWeight(i < 2 && s.isWheel ? .semibold : .regular)
                                    .monospacedDigit()
                                    .multilineTextAlignment(.trailing)
                            }
                            .font(.body)
                            .padding(.vertical, 12)
                            .accessibilityElement(children: .combine)
                        }
                    }

                    if let warning = swingCollateralWarning(s, cash: cash) {
                        Label(warning, systemImage: "banknote")
                            .font(.subheadline)
                            .foregroundStyle(DS.Palette.warning)
                    }

                    Text(decisionConfirmBody(s, review.decision))
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.horizontal, 20)
                .padding(.top, 8)
                .padding(.bottom, 24)
            }

            if phase == .sending {
                VStack(spacing: 10) {
                    ProgressView()
                    Text("Sending…")
                        .font(.headline)
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, minHeight: SwipeUpToSend.height)
                .padding(.bottom, 12)
            } else {
                SwipeUpToSend(label: "Swipe up to send", onSend: send)
                    .padding(.bottom, 12)
            }
        }
    }

    /// Premium and collateral lead for a put seller; a swing order shows its
    /// proposal.
    private func rows(_ s: SwingSignal) -> [(label: String, value: String)] {
        if s.isWheel {
            let lead = swingWheelLead(s)
            var rows: [(label: String, value: String)] = [("Premium", lead.premium), ("Collateral", lead.collateral)]
            rows += swingWheelDetails(s)
            if let otm = s.otmPct { rows.append(("Cushion", fmtItm(-otm))) }
            return rows
        }
        return swingProposalFields(s).map { (swingFieldLabel($0.label), $0.value) }
    }

    // MARK: Actions

    private func send() {
        guard phase == .review else { return }
        phase = .sending
        Task {
            let result = await review.send()
            if result.outcome == .ignored {
                phase = .review
                return
            }
            withAnimation(.snappy) { phase = .done(result) }
            if Self.isSent(result) {
                try? await Task.sleep(for: .seconds(1.8))
                close(result)
            }
        }
    }

    private func close(_ result: DecisionResult) {
        review.finished(result)
        dismiss()
    }
}

/// "Swipe up to send": drag the chevron up past the threshold (or flick it)
/// to send. VoiceOver and Switch Control send with the default action.
struct SwipeUpToSend: View {
    let label: String
    let onSend: () -> Void

    nonisolated static let height: CGFloat = 96
    nonisolated static let threshold: CGFloat = 80

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var drag: CGFloat = 0

    /// A drag up of `threshold`, or a flick predicted to travel twice that,
    /// sends.
    nonisolated static func commits(translation: CGFloat, predicted: CGFloat, threshold: CGFloat = threshold) -> Bool {
        -translation >= threshold || -predicted >= threshold * 2
    }

    var body: some View {
        let lift = min(max(0, -drag), Self.threshold * 1.25)
        let progress = lift / Self.threshold
        VStack(spacing: 8) {
            Image(systemName: "chevron.up")
                .font(.title.weight(.bold))
                .symbolEffect(.wiggle.up, options: .repeat(.periodic(delay: 1.4)), isActive: !reduceMotion && drag == 0)
            Text(label)
                .font(.headline)
        }
        .foregroundStyle(DS.Palette.accent)
        .frame(maxWidth: .infinity, minHeight: Self.height)
        .contentShape(Rectangle())
        .offset(y: -lift)
        .opacity(1 - min(progress, 1) * 0.4)
        .gesture(
            DragGesture(minimumDistance: 4)
                .onChanged { drag = $0.translation.height }
                .onEnded { value in
                    let commit = Self.commits(translation: value.translation.height, predicted: value.predictedEndTranslation.height)
                    withAnimation(.spring(response: 0.35, dampingFraction: 0.7)) { drag = 0 }
                    if commit { onSend() }
                }
        )
        .sensoryFeedback(.impact(weight: .medium), trigger: progress >= 1)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label)
        .accessibilityAddTraits(.isButton)
        .accessibilityHint("Sends the order to the broker.")
        .accessibilityAction { onSend() }
    }
}

/// The result: a green check that springs in and draws itself for a sent
/// approval; otherwise the reason it did not go.
private struct SwingOrderOutcomeView: View {
    let result: DecisionResult

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var shown = false

    private var sent: Bool { SwingOrderReviewSheet.isSent(result) }

    var body: some View {
        VStack(spacing: 20) {
            Spacer()
            ZStack {
                // One ring that expands and fades as the mark lands.
                Circle()
                    .stroke(tint.opacity(shown ? 0 : 0.5), lineWidth: 3)
                    .frame(width: 112, height: 112)
                    .scaleEffect(shown && !reduceMotion ? 1.6 : 1)
                Circle()
                    .fill(tint)
                    .frame(width: 112, height: 112)
                    .scaleEffect(shown || reduceMotion ? 1 : 0.4)
                    .opacity(shown || reduceMotion ? 1 : 0)
                if shown {
                    let mark = Image(systemName: sent ? "checkmark" : "xmark")
                        .font(.system(size: 50, weight: .bold))
                        .foregroundStyle(.white)
                    if reduceMotion {
                        mark.transition(.opacity)
                    } else {
                        mark.transition(.symbolEffect(.drawOn.byLayer))
                    }
                }
            }
            .accessibilityHidden(true)
            VStack(spacing: 8) {
                Text(title)
                    .font(.title2.weight(.bold))
                Text(result.message)
                    .font(.body)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            .padding(.horizontal, 32)
            .accessibilityElement(children: .combine)
            Spacer()
            Spacer()
        }
        .frame(maxWidth: .infinity)
        .onAppear {
            withAnimation(.spring(response: 0.5, dampingFraction: 0.6).delay(0.05)) { shown = true }
        }
    }

    private var tint: Color {
        switch result.outcome {
        case .recorded, .uncertain: DS.Palette.success
        case .noLongerPending: Color.secondary
        case .failed, .ignored: DS.Palette.danger
        }
    }

    private var title: String {
        switch result.outcome {
        case .recorded: "Sent to the broker"
        case .uncertain: "Approved"
        case .noLongerPending: "No longer pending"
        case .failed, .ignored: "Not sent"
        }
    }
}
