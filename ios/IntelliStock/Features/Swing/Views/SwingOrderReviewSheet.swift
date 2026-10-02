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
                    label: fingerY == nil ? "Swipe up to send" : (reached ? "Release to send" : "All the way up"),
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
                            withAnimation(.spring(response: 0.35, dampingFraction: 0.75)) { drag = 0 }
                        }
                    }
            )
            .sensoryFeedback(.impact(weight: .medium), trigger: reached)
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

    /// Only a drag to the very top sends: the finger ends in the top tenth
    /// of the sheet, having travelled at least 40% of its height. No flick
    /// shortcut, so a drag that stops short never sends.
    nonisolated static func reachesTop(fingerY: CGFloat, travel: CGFloat, height: CGFloat) -> Bool {
        height > 0 && fingerY <= height * 0.1 && travel >= height * 0.4
    }

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

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

/// The order itself, on a raised card: what it is, the chart, the few
/// figures that matter, and the broker's caveat. Equatable on its inputs,
/// so the sheet's drag never rebuilds it.
private struct SwingOrderCard: View, Equatable {
    let review: SwingOrderReview
    let cash: Double?

    nonisolated static func == (a: Self, b: Self) -> Bool {
        a.review.id == b.review.id && a.cash == b.cash
    }

    var body: some View {
        let s = review.signal
        let put = SwingPutPlan(s)
        VStack(alignment: .leading, spacing: 16) {
            if review.demo {
                Label("Demo · nothing is sent", systemImage: "play.circle")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(DS.Palette.info)
            }
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(s.symbol)
                        .font(.largeTitle.weight(.bold))
                    Text(put != nil ? "Sell put" : swingLaneLabel(s))
                        .font(.headline)
                        .foregroundStyle(put != nil ? DS.Palette.success : .secondary)
                    if review.decision == "approve_half" {
                        Text("½ size").font(.headline).foregroundStyle(.secondary)
                    }
                }
                if let put {
                    Text("\(put.summary) \(put.assignment)")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .accessibilityElement(children: .combine)

            SwingOrderChart(symbol: s.symbol, strike: put?.strike)

            StatGrid(columns: 2) {
                ForEach(Array(figures(s, put).enumerated()), id: \.offset) { _, row in
                    StatCell(label: row.label, value: row.value)
                }
            }

            if let warning = swingCollateralWarning(s, cash: cash) {
                Label(warning, systemImage: "banknote")
                    .font(.footnote)
                    .foregroundStyle(DS.Palette.warning)
            }

            Text(decisionConfirmBody(s, review.decision))
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(20)
        .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.card, style: .continuous))
        .padding(.horizontal, 16)
        .padding(.top, 8)
    }

    /// A put: premium, collateral, breakeven and expiry; the strike is on the
    /// chart and "set at approval" placeholders are left out. A swing order
    /// shows its proposal.
    private func figures(_ s: SwingSignal, _ put: SwingPutPlan?) -> [(label: String, value: String)] {
        if let put {
            let lead = swingWheelLead(s)
            var rows: [(label: String, value: String)] = [("Premium", lead.premium), ("Collateral", lead.collateral)]
            if let be = put.breakeven { rows.append(("Breakeven", fmtMoney(be))) }
            if let e = put.expiry { rows.append(("Expires", stockExpiryText(e))) }
            return rows
        }
        return swingProposalFields(s).map { (swingFieldLabel($0.label), $0.value) }
    }
}

/// The underlying's price over a chosen range. For a put, the strike is one
/// labelled dashed line over a faint red assignment zone. Scrub to read a
/// price; read-only, nothing is sent.
private struct SwingOrderChart: View {
    let symbol: String
    let strike: Double?

    @Environment(AppServices.self) private var services

    var body: some View {
        SwingOrderChartContent(symbol: symbol, strike: strike, services: services)
            .id(symbol)
    }
}

private struct SwingOrderChartContent: View {
    let strike: Double?
    let services: AppServices

    @State private var model: StockModel

    static let ranges = ["1D", "1W", "1M", "3M", "1Y"]
    static let height: CGFloat = 160

    init(symbol: String, strike: Double?, services: AppServices) {
        self.strike = strike
        self.services = services
        _model = State(initialValue: StockModel(symbol: symbol, brokerageId: nil, client: { services.apiClient }))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Separate views: a scrub re-renders only the price, never the plot.
            SwingOrderPriceLine(model: model, strike: strike)
            SwingOrderPlot(model: model, strike: strike, height: Self.height)
            Picker("Range", selection: Binding(get: { model.range }, set: { model.setRange($0) })) {
                ForEach(Self.ranges, id: \.self) { Text($0).tag($0) }
            }
            .pickerStyle(.segmented)
            .controlSize(.small)
        }
        .task(id: model.range) { await model.pollHistory(lifecycle: services.lifecycle) }
    }
}

/// "$184.91 · 4.17% above strike", following the scrub.
private struct SwingOrderPriceLine: View {
    let model: StockModel
    let strike: Double?

    var body: some View {
        if let vals = model.series?.vals, vals.count >= 2 {
            let idx = model.scrubIndex.flatMap { (0..<vals.count).contains($0) ? $0 : nil }
            let shown = vals[idx ?? vals.count - 1]
            let change = shown - vals[0]
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(fmtMoney(shown))
                    .font(.title3.weight(.semibold))
                    .monospacedDigit()
                if let strike, strike > 0 {
                    let cushion = (shown - strike) / strike * 100
                    Text(cushion >= 0 ? "\(fmtPct(cushion)) above strike" : "\(fmtPct(cushion)) below strike")
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(cushion >= 0 ? DS.Palette.success : DS.Palette.danger)
                } else {
                    Text("\(fmtPnl(change)) (\(fmtPct(vals[0] != 0 ? change / vals[0] * 100 : 0)))")
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(ChangeDirection(change).color)
                }
            }
            .monospacedDigit()
            .accessibilityElement(children: .combine)
        } else {
            Text("\(model.symbol) price")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.secondary)
        }
    }
}

/// The plot. Reads only the series and range, never the scrub index.
private struct SwingOrderPlot: View {
    let model: StockModel
    let strike: Double?
    let height: CGFloat

    var body: some View {
        if let series = model.series, series.vals.count >= 2 {
            let up = series.vals[series.vals.count - 1] >= series.vals[0]
            let model = model
            ScrubbableAreaChart(
                timestamps: series.ts,
                values: series.vals,
                lineColor: up ? DS.Palette.success : DS.Palette.danger,
                height: height,
                onScrub: { model.scrubIndex = $0 },
                animate: true,
                drawInKey: AnyHashable(model.range),
                indexed: true,
                showsBaseline: strike == nil,
                levels: strike.map { [ScrubbableChartLevel(value: $0, label: "Strike \(fmtMoney($0))", color: .secondary)] } ?? [],
                bands: strike.map { [ScrubbableChartBand(low: nil, high: $0, color: DS.Palette.danger)] } ?? []
            )
            .id(model.range)
        } else if model.historyLoading {
            Skeleton(height: height, radius: 10)
        } else {
            Text("Couldn't load prices for \(model.symbol)")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, minHeight: height)
        }
    }
}
