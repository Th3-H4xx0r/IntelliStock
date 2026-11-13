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
/// brokerage's "swipe up to submit", styled as the web's order review
/// (`SwingOrderReviewModal.vue`): the symbol and side over "Review the order
/// before it goes to the broker", the plan, the chart, the figures, the
/// broker's caveat verbatim, then a swipe up that names the order ("Approve
/// sell 1 QCOM put") and sends it. A recorded approval plays the sent mark
/// and closes; a failure stays open with its reason.
struct SwingOrderReviewSheet: View {
    let review: SwingOrderReview
    let cash: Double?

    @Environment(\.dismiss) private var dismiss
    @State private var phase: Phase = .review
    /// The card's vertical drag; negative is up.
    @State private var drag: CGFloat = 0
    /// The card has flown off the top.
    @State private var launched = false
    /// Where the finger is during a drag, in the sheet's (unmoving) space.
    @State private var fingerY: CGFloat?

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
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(DS.Surface.page)
            .navigationTitle(phase == .review || phase == .sending ? "Review order" : "")
            .navigationBarTitleDisplayMode(.inline)
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
        .presentationBackground(DS.Surface.page)
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
    /// the finger up, and only a drag that reaches the very top sends: the
    /// finger must end in the top tenth of the sheet, having travelled at
    /// least 40% of it. Anything short of that, a flick included, springs
    /// back. A drag down only stretches a little.
    private var reviewContent: some View {
        GeometryReader { geo in
            let height = geo.size.height
            let reached = fingerY.map { SwipeUpHint.reachesTop(fingerY: $0, travel: -drag, height: height) } ?? false
            let lift = drag < 0 ? drag : drag * 0.2
            let progress: CGFloat = reached ? 1 : min(max(0, -drag) / max(1, height * 0.6), 0.95)
            VStack(spacing: 0) {
                // Its own view, so a drag moves it without rebuilding it.
                let card = SwingOrderCard(review: review, cash: cash).equatable()
                ViewThatFits(in: .vertical) {
                    card
                    ScrollView { card }
                }
                Spacer(minLength: 0)
                SwipeUpHint(
                    label: fingerY == nil ? swingReviewActionLabel(review.signal, review.decision) : (reached ? "Release to send" : "All the way up"),
                    progress: progress,
                    onSend: launch
                )
            }
            .padding(.bottom, 12)
            .offset(y: launched ? -1400 : lift)
            .opacity(launched ? 0 : 1)
            .scaleEffect(launched ? 0.92 : 1, anchor: .top)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 8, coordinateSpace: .named(Self.space))
                    .onChanged { value in
                        guard phase == .review, !launched else { return }
                        drag = value.translation.height
                        fingerY = value.location.y
                    }
                    .onEnded { value in
                        guard phase == .review, !launched else { return }
                        let sends = SwipeUpHint.reachesTop(fingerY: value.location.y, travel: -value.translation.height, height: height)
                        fingerY = nil
                        if sends {
                            launch()
                        } else {
                            withAnimation(DSMotion.ease(0.3)) { drag = 0 }
                        }
                    }
            )
            .sensoryFeedback(.impact(weight: .medium), trigger: reached)
            .overlay {
                if phase == .sending {
                    VStack(spacing: 10) {
                        ProgressView()
                        Text("Sending…")
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.secondary)
                    }
                    .transition(.opacity)
                }
            }
        }
        .coordinateSpace(.named(Self.space))
    }

    private static let space = "swing.order.review"

    // MARK: Actions

    /// The card flies off the top, then the order sends.
    private func launch() {
        guard phase == .review, !launched else { return }
        withAnimation(.easeIn(duration: 0.3)) { launched = true }
        withAnimation(.easeIn(duration: 0.2).delay(0.15)) { phase = .sending }
        Task {
            let result = await review.send()
            if result.outcome == .ignored {
                withAnimation(DSMotion.ease(0.35)) {
                    phase = .review
                    launched = false
                    drag = 0
                }
                return
            }
            withAnimation(DSMotion.ease(0.25)) { phase = .done(result) }
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

// `SwipeUpHint` (the hint under the order card) lives in the design system:
// DesignSystem/Components/SwipeUpHint.swift, shared with the trade review.

/// The result (the web's result step): a gain check that draws itself in
/// for a sent approval, a loss mark for one that did not go, a quiet info
/// mark when it is no longer pending; then the title and the server's words.
private struct SwingOrderOutcomeView: View {
    let result: DecisionResult

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var shown = false

    var body: some View {
        VStack(spacing: 12) {
            Spacer()
            ZStack {
                if shown {
                    let mark = Image(systemName: symbol)
                        .font(.system(size: 40, weight: .light))
                        .foregroundStyle(tint)
                    if reduceMotion {
                        mark.transition(.opacity)
                    } else {
                        mark.transition(.symbolEffect(.drawOn.byLayer))
                    }
                }
            }
            .frame(height: 48)
            .accessibilityHidden(true)
            VStack(spacing: 8) {
                Text(title)
                    .font(DS.Font.large)
                    .foregroundStyle(DS.Text.primary)
                Text(result.message)
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.secondary)
                    .lineSpacing(3)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 320)
            }
            .padding(.horizontal, 32)
            .accessibilityElement(children: .combine)
            Spacer()
            Spacer()
        }
        .frame(maxWidth: .infinity)
        .onAppear {
            withAnimation(DSMotion.ease(0.4).delay(0.05)) { shown = true }
        }
    }

    private var symbol: String {
        switch result.outcome {
        case .recorded, .uncertain: "checkmark.circle"
        case .noLongerPending: "info.circle"
        case .failed, .ignored: "xmark.circle"
        }
    }

    private var tint: Color {
        switch result.outcome {
        case .recorded, .uncertain: DS.Palette.gain
        case .noLongerPending: DS.Text.secondary
        case .failed, .ignored: DS.Palette.loss
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

/// What selling the put means: the strike, the premium and the breakeven
/// (strike − premium per share).
struct SwingPutPlan: Equatable {
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

    /// "Collect about $131.00 if QCOM stays above $177.50 by Oct 9, 2026."
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

/// The order itself, on the modal's card: the head (symbol, side, "Review
/// the order before it goes to the broker"), then the plan, the chart, the
/// figures that matter, the collateral warning and the broker's caveat — the
/// web review's order. Equatable on its inputs, so the sheet's drag never
/// rebuilds it.
private struct SwingOrderCard: View, Equatable {
    let review: SwingOrderReview
    let cash: Double?

    nonisolated static func == (a: Self, b: Self) -> Bool {
        a.review.id == b.review.id && a.cash == b.cash
    }

    var body: some View {
        let s = review.signal
        let put = SwingPutPlan(s)
        VStack(alignment: .leading, spacing: 0) {
            // Head (the web's `ui-modal-head`).
            VStack(alignment: .leading, spacing: 4) {
                if review.demo {
                    Text("Demo · nothing is sent")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.secondary)
                        .padding(.bottom, 4)
                }
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    Text(s.symbol)
                        .font(DS.Font.pageTitle)
                        .foregroundStyle(DS.Text.primary)
                    Text(s.isWheel ? "Sell put" : "Buy")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.label)
                    if review.decision == "approve_half" {
                        Text("½ size")
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.secondary)
                    }
                }
                Text("Review the order before it goes to the broker")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
            }
            .padding(.horizontal, 24)
            .padding(.top, 20)
            .padding(.bottom, 16)
            .accessibilityElement(children: .combine)
            DSDivider()

            VStack(alignment: .leading, spacing: 20) {
                if let put {
                    Text("\(put.summary) \(put.assignment)")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.label)
                        .lineSpacing(3)
                        .fixedSize(horizontal: false, vertical: true)
                }

                SwingOrderChart(symbol: s.symbol, strike: put?.strike)

                StatGrid(columns: 2, horizontalSpacing: 16, verticalSpacing: 16) {
                    ForEach(Array(figures(s, put).enumerated()), id: \.offset) { _, row in
                        StatCell(label: row.label, footnote: row.foot) {
                            Text(row.value).font(DS.Font.large)
                        }
                    }
                }

                if let warning = swingCollateralWarning(s, cash: cash) {
                    HStack(alignment: .top, spacing: 8) {
                        DSStatusDot(.warn)
                            .padding(.top, 3)
                        Text(warning)
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.label)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .accessibilityElement(children: .combine)
                }

                Text(decisionConfirmBody(s, review.decision))
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 20)
        }
        .dsCardSurface(radius: DS.Radius.modal, border: DS.Line.input)
        .padding(.horizontal, 16)
        .padding(.top, 8)
    }

    /// A put: premium (a share under it), collateral (the return under it),
    /// breakeven and expiry; the strike is on the chart and "set at approval"
    /// placeholders are left out. A swing order shows its proposal.
    private func figures(_ s: SwingSignal, _ put: SwingPutPlan?) -> [(label: String, value: String, foot: String?)] {
        if let put {
            let lead = swingWheelLead(s)
            var rows: [(label: String, value: String, foot: String?)] = [
                ("Premium", lead.premium, lead.premiumFootnote),
                ("Collateral", lead.collateral, lead.collateralFootnote),
            ]
            if let be = put.breakeven { rows.append(("Breakeven", fmtMoney(be), nil)) }
            if let e = put.expiry { rows.append(("Expires", stockExpiryText(e), nil)) }
            return rows
        }
        return swingProposalFields(s).map { (swingFieldLabel($0.label), $0.value, nil) }
    }
}

/// The underlying's price over a chosen range (the web's `SwingOrderChart`):
/// the price and its cushion to the strike, the range control, then the
/// plot. For a put, the strike is one labelled dashed line over a faint loss
/// assignment zone. Scrub to read a price; read-only, nothing is sent.
struct SwingOrderChart: View {
    let symbol: String
    let strike: Double?
    /// Where the assignment zone lies: below the strike for a sold put,
    /// above it for a sold call; nil draws only the strike line.
    var riskBelow: Bool? = true

    @Environment(AppServices.self) private var services

    var body: some View {
        SwingOrderChartContent(symbol: symbol, strike: strike, riskBelow: riskBelow, services: services)
            .id(symbol)
    }
}

private struct SwingOrderChartContent: View {
    let strike: Double?
    let riskBelow: Bool?
    let services: AppServices

    @State private var model: StockModel

    static let ranges = ["1D", "1W", "1M", "3M", "1Y"]
    static let height: CGFloat = 160

    init(symbol: String, strike: Double?, riskBelow: Bool?, services: AppServices) {
        self.strike = strike
        self.riskBelow = riskBelow
        self.services = services
        _model = State(initialValue: StockModel(symbol: symbol, brokerageId: nil, client: { services.apiClient }))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Separate views: a scrub re-renders only the price, never the plot.
            SwingOrderPriceLine(model: model, strike: strike)
            DSSegmentedControl(
                "Chart range",
                selection: Binding(get: { model.range }, set: { model.setRange($0) }),
                options: Self.ranges
            ) { $0 }
            SwingOrderPlot(model: model, strike: strike, riskBelow: riskBelow, height: Self.height)
                .padding(.top, 4)
        }
        .task(id: model.range) { await model.pollHistory(lifecycle: services.lifecycle) }
    }
}

/// "$184.91" over "4.17% above strike", following the scrub; without a
/// strike, the change since the range began and the range ("Today").
private struct SwingOrderPriceLine: View {
    let model: StockModel
    let strike: Double?

    private static let captions = ["1D": "Today", "1W": "Past week", "1M": "Past month", "3M": "Past 3 months", "1Y": "Past year"]

    var body: some View {
        if let vals = model.series?.vals, vals.count >= 2 {
            let idx = model.scrubIndex.flatMap { (0..<vals.count).contains($0) ? $0 : nil }
            let shown = vals[idx ?? vals.count - 1]
            let change = shown - vals[0]
            VStack(alignment: .leading, spacing: 6) {
                Text(fmtMoney(shown))
                    .font(DS.Font.figureLarge)
                    .foregroundStyle(DS.Text.primary)
                if let strike, strike > 0 {
                    let cushion = (shown - strike) / strike * 100
                    Text("\(dartToStringAsFixed(abs(cushion), 2))% \(cushion >= 0 ? "above" : "below") strike")
                        .font(DS.Font.body)
                        .foregroundStyle(cushion >= 0 ? DS.Palette.gain : DS.Palette.loss)
                } else {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text("\(fmtPnl(change)) (\(fmtPct(vals[0] != 0 ? change / vals[0] * 100 : 0)))")
                            .foregroundStyle(ChangeDirection(change).color)
                        if idx == nil, let caption = Self.captions[model.range] {
                            Text(caption)
                                .foregroundStyle(DS.Text.muted)
                        }
                    }
                    .font(DS.Font.body)
                }
            }
            .monospacedDigit()
            .accessibilityElement(children: .combine)
        } else {
            Text("\(model.symbol) price")
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.secondary)
        }
    }
}

/// The plot. Reads only the series and range, never the scrub index.
private struct SwingOrderPlot: View {
    let model: StockModel
    let strike: Double?
    let riskBelow: Bool?
    let height: CGFloat

    private var band: ScrubbableChartBand? {
        guard let strike, let riskBelow else { return nil }
        return riskBelow
            ? ScrubbableChartBand(low: nil, high: strike, color: DS.Palette.loss)
            : ScrubbableChartBand(low: strike, high: nil, color: DS.Palette.loss)
    }

    var body: some View {
        if let series = model.series, series.vals.count >= 2 {
            let up = series.vals[series.vals.count - 1] >= series.vals[0]
            let model = model
            ScrubbableAreaChart(
                timestamps: series.ts,
                values: series.vals,
                lineColor: up ? DS.Palette.gain : DS.Palette.loss,
                height: height,
                onScrub: { model.scrubIndex = $0 },
                animate: true,
                drawInKey: AnyHashable(model.range),
                indexed: true,
                showsBaseline: strike == nil,
                levels: strike.map { [ScrubbableChartLevel(value: $0, label: "Strike \(fmtMoney($0))", color: DS.Palette.accent.opacity(0.45))] } ?? [],
                bands: band.map { [$0] } ?? [],
                showsYAxis: true
            )
            .id(model.range)
        } else if model.historyLoading {
            Skeleton(height: height, radius: DS.Radius.control)
        } else {
            Text("Couldn't load prices for \(model.symbol)")
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.muted)
                .frame(maxWidth: .infinity, minHeight: height)
        }
    }
}
