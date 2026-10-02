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
    /// A rehearsal: a sample order, and `send` never reaches the server.
    var demo = false

    /// The sheet with a sample wheel put; sending only plays the animation.
    static func demo() -> SwingOrderReview {
        SwingOrderReview(
            signal: SwingSignal(json: [
                "id": "demo",
                "lane": "wheel",
                "symbol": "QCOM",
                "session": "2026-10-02",
                "created_at": "2026-10-02T14:45:33Z",
                "score": 70,
                "recommendation": "REVIEW",
                "reasoning": "A sample order for a demo.",
                "key_risks": [],
                "proposal": ["contract": nil, "strike": 177.5, "expiry": "2026-10-09", "qty": 1,
                             "limit_price": nil, "premium_est": 1.31],
                "status": "pending",
            ]),
            decision: "approve",
            send: {
                try? await Task.sleep(for: .milliseconds(700))
                return DecisionResult(.recorded, "Demo: nothing was sent.")
            },
            finished: { _ in },
            demo: true
        )
    }
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
    /// The card's vertical drag; negative is up.
    @State private var drag: CGFloat = 0
    /// The card has flown off the top.
    @State private var launched = false

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

    /// The order card and the swipe hint under it. The whole card follows
    /// the finger up; past the threshold it flies off the top and the order
    /// sends. A drag down only stretches a little.
    private var reviewContent: some View {
        let lift = drag < 0 ? drag : drag * 0.2
        let progress = min(max(0, -drag) / SwipeUpHint.threshold, 1)
        return VStack(spacing: 0) {
            ViewThatFits(in: .vertical) {
                card
                ScrollView { card }
            }
            Spacer(minLength: 0)
            SwipeUpHint(label: "Swipe up to send", progress: progress, onSend: launch)
        }
        .padding(.bottom, 12)
        .offset(y: launched ? -1400 : lift)
        .opacity(launched ? 0 : 1)
        .scaleEffect(launched ? 0.92 : 1, anchor: .top)
        .contentShape(Rectangle())
        .gesture(
            DragGesture(minimumDistance: 8)
                .onChanged { value in
                    guard phase == .review, !launched else { return }
                    drag = value.translation.height
                }
                .onEnded { value in
                    guard phase == .review, !launched else { return }
                    if SwipeUpHint.commits(translation: value.translation.height, predicted: value.predictedEndTranslation.height) {
                        launch()
                    } else {
                        withAnimation(.spring(response: 0.35, dampingFraction: 0.75)) { drag = 0 }
                    }
                }
        )
        .sensoryFeedback(.impact(weight: .medium), trigger: progress >= 1)
        .overlay {
            if phase == .sending {
                VStack(spacing: 10) {
                    ProgressView()
                    Text("Sending…")
                        .font(.headline)
                        .foregroundStyle(.secondary)
                }
                .transition(.opacity)
            }
        }
    }

    /// The order itself, on a raised card.
    private var card: some View {
        let s = review.signal
        return VStack(alignment: .leading, spacing: 18) {
            if review.demo {
                Label("Demo · nothing is sent", systemImage: "play.circle")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(DS.Palette.info)
            }
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

            SwingOrderChart(symbol: s.symbol, put: SwingPutPlan(s))

            StatGrid(columns: 2) {
                ForEach(Array(rows(s).enumerated()), id: \.offset) { _, row in
                    StatCell(label: row.label, value: row.value)
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
        .padding(20)
        .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.card, style: .continuous))
        .padding(.horizontal, 16)
        .padding(.top, 8)
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

    /// The card flies off the top, then the order sends.
    private func launch() {
        guard phase == .review, !launched else { return }
        withAnimation(.easeIn(duration: 0.3)) { launched = true }
        withAnimation(.easeIn(duration: 0.2).delay(0.15)) { phase = .sending }
        Task {
            let result = await review.send()
            if result.outcome == .ignored {
                withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) {
                    phase = .review
                    launched = false
                    drag = 0
                }
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

/// The hint under the order card: a chevron and "Swipe up to send" that
/// brighten as the card nears the threshold. The card's drag does the
/// sending; VoiceOver and Switch Control send with the default action.
struct SwipeUpHint: View {
    let label: String
    /// 0 at rest, 1 at the threshold.
    let progress: CGFloat
    let onSend: () -> Void

    nonisolated static let threshold: CGFloat = 120

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// A drag up of `threshold`, or a flick predicted to travel twice that,
    /// sends.
    nonisolated static func commits(translation: CGFloat, predicted: CGFloat, threshold: CGFloat = threshold) -> Bool {
        -translation >= threshold || -predicted >= threshold * 2
    }

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: "chevron.up")
                .font(.title.weight(.bold))
                .symbolEffect(.wiggle.up, options: .repeat(.periodic(delay: 1.4)), isActive: !reduceMotion && progress == 0)
            Text(progress >= 1 ? "Release to send" : label)
                .font(.headline)
        }
        .foregroundStyle(DS.Palette.accent)
        .opacity(0.7 + 0.3 * progress)
        .frame(maxWidth: .infinity, minHeight: 88)
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

/// What selling the put means, for the chart: the strike, the premium and
/// the breakeven (strike − premium per share).
struct SwingPutPlan {
    let symbol: String
    let strike: Double
    /// Per share.
    let premium: Double?
    let contracts: Int
    let expiry: String?

    init?(_ s: SwingSignal) {
        guard s.isWheel, let strike = s.proposal["strike"]?.double, strike > 0 else { return nil }
        symbol = s.symbol
        self.strike = strike
        premium = s.proposal["premium_est"]?.double
        contracts = max(1, s.proposal["qty"]?.int ?? 1)
        expiry = s.proposal["expiry"]?.string
    }

    var breakeven: Double? { premium.map { strike - $0 } }
    /// Total premium collected, e.g. $131 for one contract at $1.31.
    var collected: Double? { premium.map { $0 * 100 * Double(contracts) } }

    /// "Collect $131 if QCOM stays above $177.50 by Oct 9, 2026."
    var summary: String {
        let by = expiry.map { " by \(stockExpiryText($0))" } ?? ""
        let collect = collected.map { "Collect about \(fmtMoney($0))" } ?? "Collect the premium"
        return "\(collect) if \(symbol) stays above \(fmtMoney(strike))\(by)."
    }

    /// "Below $177.50 you buy 100 shares at $177.50."
    var assignment: String {
        "Below \(fmtMoney(strike)) you buy \(contracts * 100) shares at \(fmtMoney(strike))."
    }
}

/// The underlying's price over a chosen range, for insight before sending.
/// For a put, the strike and breakeven are labelled lines over a green
/// keep-the-premium zone and a red assignment zone. Scrub to read a price;
/// read-only, nothing is sent.
private struct SwingOrderChart: View {
    let symbol: String
    let put: SwingPutPlan?

    @Environment(AppServices.self) private var services

    var body: some View {
        SwingOrderChartContent(symbol: symbol, put: put, services: services)
            .id(symbol)
    }
}

private struct SwingOrderChartContent: View {
    let put: SwingPutPlan?
    let services: AppServices

    @State private var model: StockModel

    static let ranges = ["1D", "1W", "1M", "3M", "1Y"]
    static let height: CGFloat = 170

    init(symbol: String, put: SwingPutPlan?, services: AppServices) {
        self.put = put
        self.services = services
        _model = State(initialValue: StockModel(symbol: symbol, brokerageId: nil, client: { services.apiClient }))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let put {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("SELL PUT")
                        .font(.caption.weight(.bold))
                        .padding(.horizontal, 7)
                        .padding(.vertical, 3)
                        .foregroundStyle(DS.Palette.success)
                        .background(DS.Palette.success.opacity(0.15), in: .capsule)
                    Text(put.summary)
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .accessibilityElement(children: .combine)
            }
            header
            chart
            Picker("Range", selection: Binding(get: { model.range }, set: { model.setRange($0) })) {
                ForEach(Self.ranges, id: \.self) { Text($0).tag($0) }
            }
            .pickerStyle(.segmented)
            .controlSize(.small)
            if let put {
                Label(put.assignment, systemImage: "arrow.down.right.circle")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .task(id: model.range) { await model.pollHistory(lifecycle: services.lifecycle) }
    }

    /// The price (scrubbed, or the latest) and its change over the range;
    /// for a put, how far it sits above the strike.
    @ViewBuilder
    private var header: some View {
        if let vals = model.series?.vals, vals.count >= 2 {
            let idx = model.scrubIndex.flatMap { (0..<vals.count).contains($0) ? $0 : nil }
            let shown = vals[idx ?? vals.count - 1]
            let change = shown - vals[0]
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(fmtMoney(shown))
                    .font(.title3.weight(.semibold))
                    .monospacedDigit()
                Text("\(fmtPnl(change)) (\(fmtPct(vals[0] != 0 ? change / vals[0] * 100 : 0)))")
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(ChangeDirection(change).color)
                    .monospacedDigit()
                Spacer(minLength: 0)
                if let put {
                    let cushion = (shown - put.strike) / put.strike * 100
                    Text(cushion >= 0 ? "\(fmtPct(cushion)) above strike" : "\(fmtPct(cushion)) below strike")
                        .font(.footnote.weight(.medium))
                        .foregroundStyle(cushion >= 0 ? DS.Palette.success : DS.Palette.danger)
                        .monospacedDigit()
                }
            }
            .accessibilityElement(children: .combine)
        } else {
            Text("\(model.symbol) price")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private var chart: some View {
        if let series = model.series, series.vals.count >= 2 {
            let up = series.vals[series.vals.count - 1] >= series.vals[0]
            ScrubbableAreaChart(
                timestamps: series.ts,
                values: series.vals,
                lineColor: up ? DS.Palette.success : DS.Palette.danger,
                height: Self.height,
                onScrub: { model.scrubIndex = $0 },
                animate: true,
                drawInKey: AnyHashable(model.range),
                indexed: true,
                showsBaseline: put == nil,
                levels: levels,
                bands: bands
            )
            .id(model.range)
        } else if model.historyLoading {
            Skeleton(height: Self.height, radius: 10)
        } else {
            Text("Couldn't load prices for \(model.symbol)")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, minHeight: Self.height)
        }
    }

    private var levels: [ScrubbableChartLevel] {
        guard let put else { return [] }
        var out = [ScrubbableChartLevel(value: put.strike, label: "Strike \(fmtMoney(put.strike))", color: .primary)]
        if let be = put.breakeven {
            out.append(ScrubbableChartLevel(value: be, label: "Breakeven \(fmtMoney(be))", color: DS.Palette.warning))
        }
        return out
    }

    private var bands: [ScrubbableChartBand] {
        guard let put else { return [] }
        let keep = put.collected.map { "Keep \(fmtMoney($0))" } ?? "Keep the premium"
        return [
            ScrubbableChartBand(low: put.strike, high: nil, color: DS.Palette.success, label: keep),
            ScrubbableChartBand(low: nil, high: put.strike, color: DS.Palette.danger, label: "Assigned"),
        ]
    }
}
