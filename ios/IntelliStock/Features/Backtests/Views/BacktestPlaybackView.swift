import SwiftUI

/// The backtest replay (`/backtests/:id/playback`) — the web's
/// `BacktestPlaybackView.vue`: "Backtest playback" over "Backtest #id", the
/// portfolio value card (figure, holdings, chart) over the execution log,
/// which grows frame by frame as a stepper. The transport (the replay's
/// date, play / pause, reset and speed) sits in a bar at the bottom.
struct BacktestPlaybackView: View {
    let id: String

    @Environment(AppServices.self) private var services
    @State private var model: BacktestPlaybackModel?

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// `model` hands in an already loaded replay (the snapshot harness);
    /// without one the screen builds and loads its own.
    init(id: String, model: BacktestPlaybackModel? = nil) {
        self.id = id
        _model = State(initialValue: model)
    }

    var body: some View {
        Group {
            if let model {
                if model.loading {
                    skeleton
                } else if let error = model.error {
                    message(title: "Failed to load playback", body: error)
                } else if model.isEmpty {
                    message(title: "No playback data", body: "This backtest has no portfolio snapshots or trades to replay")
                } else {
                    playback(model)
                }
            } else {
                Color.clear
            }
        }
        .background(DS.Surface.page)
        .navigationTitle("Backtest playback")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                VStack(spacing: 0) {
                    Text("Backtest playback")
                        .font(DS.Font.bodyMedium)
                        .foregroundStyle(DS.Text.primary)
                    Text("Backtest #\(id)")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                }
                .accessibilityElement(children: .combine)
            }
        }
        .task(id: id) {
            if model == nil {
                model = BacktestPlaybackModel(repository: { [services] in services.backtestRepository })
            }
            if let model, model.needsLoad { await model.load(id) }
        }
        .onDisappear { model?.stop() }
    }

    private func back() {
        if services.router.stack(for: services.router.tab).isEmpty {
            services.router.go("/backtests/\(id)")
        } else {
            services.router.pop()
        }
    }

    /// The loading shape: the portfolio card and the log, as blocks.
    private var skeleton: some View {
        VStack(spacing: 12) {
            Skeleton(height: 220, radius: DS.Radius.card)
            RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous)
                .fill(DS.Surface.raised)
                .frame(maxHeight: .infinity)
        }
        .padding(.horizontal, DS.pageGutter)
        .padding(.vertical, 8)
        .accessibilityLabel("Loading")
    }

    /// An empty or failed replay (`ui-empty` in a card): what happened and
    /// the way back.
    private func message(title: String, body: String) -> some View {
        VStack {
            Card(padding: 0) {
                EmptyState(title: title, subtitle: body, actionLabel: "Back to backtest", onAction: back)
            }
            Spacer()
        }
        .padding(.horizontal, DS.pageGutter)
        .padding(.top, 8)
    }

    // MARK: Body

    private func playback(_ model: BacktestPlaybackModel) -> some View {
        VStack(spacing: 12) {
            portfolioPanel(model)
            eventLog(model)
        }
        .padding(.horizontal, DS.pageGutter)
        .padding(.top, 8)
        .padding(.bottom, 12)
        .safeAreaInset(edge: .bottom, spacing: 0) { controls(model) }
    }

    /// The web header's controls: the replay's date, then play / pause (the
    /// primary button), reset and the speed.
    private func controls(_ model: BacktestPlaybackModel) -> some View {
        HStack(spacing: 6) {
            Text(model.currentDateLabel)
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.label)
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .frame(maxWidth: .infinity, alignment: .leading)
            Button {
                model.togglePlay()
            } label: {
                Image(systemName: Symbol.named(model.isPlaying ? "pause" : "play_arrow"))
                    .font(.system(size: 18))
                    .frame(width: 20)
                    .contentTransition(.symbolEffect(.replace))
            }
            .buttonStyle(.dsPrimary)
            .accessibilityLabel(model.isPlaying ? "Pause" : "Play")
            Button {
                model.reset()
            } label: {
                Image(systemName: Symbol.named("replay"))
                    .font(.system(size: 16))
                    .frame(width: 20)
            }
            .buttonStyle(.dsSecondary(size: .medium))
            .accessibilityLabel("Reset")
            Button {
                model.cycleSpeed()
            } label: {
                Text(model.speedLabel)
                    .monospacedDigit()
                    .frame(minWidth: 24)
            }
            .buttonStyle(.dsSecondary(size: .medium))
            .accessibilityLabel("Speed \(model.speedLabel)")
        }
        .padding(.horizontal, DS.pageGutter)
        .padding(.vertical, 12)
        .background {
            DS.Surface.page
                .overlay(alignment: .top) { DSDivider() }
                .ignoresSafeArea(edges: .bottom)
        }
        .sensoryFeedback(.selection, trigger: model.speedIndex)
    }

    // MARK: Portfolio panel

    /// "Portfolio value": the replayed value, the holdings at that frame,
    /// then the chart, gain or loss against the starting cash.
    private func portfolioPanel(_ model: BacktestPlaybackModel) -> some View {
        let points = model.chartPoints()
        let start = model.metadata.initialCash?.double ?? points.first?.value ?? 0
        let up = (points.last?.value ?? start) >= start
        return Card {
            VStack(alignment: .leading, spacing: 0) {
                Text("Portfolio value")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                Text(fmtMoney(model.currentPortfolioValue.double))
                    .font(DS.Font.figureLarge)
                    .foregroundStyle(DS.Text.primary)
                    .monospacedDigit()
                    .contentTransition(.numericText(value: model.currentPortfolioValue.double))
                    .animation(reduceMotion ? nil : DSMotion.standard, value: model.currentPortfolioValue.double)
                    .padding(.top, 8)
                holdings(model.currentHoldings)
                    .padding(.top, 8)
                ScrubbableAreaChart(
                    timestamps: points.map(\.time),
                    values: points.map(\.value),
                    lineColor: up ? DS.Palette.gain : DS.Palette.loss,
                    height: 140,
                    // Draws in once as it appears. Each playback tick only
                    // appends points, so it never replays.
                    baseline: model.metadata.initialCash?.double
                )
                .padding(.top, 12)
            }
        }
    }

    /// The current holdings (the web's "Current holdings" rows, compact):
    /// ticker, shares, and the price in gain or loss against the average
    /// cost; "No active positions" when flat.
    @ViewBuilder
    private func holdings(_ holdings: [PlaybackHolding]) -> some View {
        if holdings.isEmpty {
            Text("No active positions")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
        } else {
            FlowLayout(spacing: 16, runSpacing: 4) {
                ForEach(Array(holdings.prefix(6).enumerated()), id: \.offset) { _, h in
                    HStack(spacing: 6) {
                        Text(h.ticker)
                            .font(DS.Font.captionMedium)
                            .foregroundStyle(DS.Text.primary)
                        if let q = h.qty {
                            Text("\(dartToStringAsFixed(q.double, 0)) sh")
                                .foregroundStyle(DS.Text.muted)
                        }
                        if let curr = h.curr, let avg = h.avg {
                            Text(fmtMoney(curr.double))
                                .foregroundStyle(curr > avg ? DS.Palette.gain : (curr < avg ? DS.Palette.loss : DS.Text.label))
                        }
                    }
                    .font(DS.Font.caption)
                    .monospacedDigit()
                    .accessibilityElement(children: .combine)
                }
            }
        }
    }

    // MARK: Event log

    private func eventLog(_ model: BacktestPlaybackModel) -> some View {
        let events = model.visibleEvents
        return VStack(alignment: .leading, spacing: 0) {
            Text("Execution log")
                .font(DS.Font.bodyMedium)
                .foregroundStyle(DS.Text.soft)
                .accessibilityAddTraits(.isHeader)
                .padding(.horizontal, DS.rowPadding)
                .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
            DSDivider()
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 16) {
                        ForEach(Array(events.enumerated()), id: \.offset) { i, ev in
                            BacktestPlaybackEventNode(event: ev, isLast: i == events.count - 1 && !model.isPlaying).id(i)
                        }
                        if model.isPlaying {
                            typingNode.id(-2)
                        }
                        Color.clear.frame(height: 1).id(-1)
                    }
                    .padding(DS.rowPadding)
                }
                .onChange(of: model.frameIndex) {
                    withAnimation(reduceMotion ? nil : DSMotion.ease(0.3)) { proxy.scrollTo(-1, anchor: .bottom) }
                }
            }
        }
        .frame(maxHeight: .infinity)
        .dsCardSurface()
    }

    /// Playing: the next node's dot and a skeleton line.
    private var typingNode: some View {
        HStack(alignment: .top, spacing: 16) {
            Circle()
                .fill(DS.Chart.series[2])
                .frame(width: 8, height: 8)
                .frame(width: 12)
                .padding(.top, 4)
            Skeleton(width: 120, height: 16)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Playing")
    }
}

/// One execution-log node (the web's stepper): date markers between
/// hairlines; a strategy, outcome or decision as a dot on the rail with its
/// content beside it (strategies and decisions in a bordered box).
/// Portfolio events are hidden.
struct BacktestPlaybackEventNode: View {
    let event: PlaybackEvent
    var isLast = false

    var body: some View {
        switch event.type {
        case "date": dateMarker
        case "strategy": strategy
        case "outcome": outcome
        case "decision": decision
        default: EmptyView()
        }
    }

    private var dateMarker: some View {
        HStack(spacing: 12) {
            DSDivider()
            Text("\(event.label ?? "") — \(event.time ?? "")")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.secondary)
                .monospacedDigit()
                .fixedSize()
            DSDivider()
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }

    /// The rail: a dot (filled, or a ring for an outcome) and the line down
    /// to the next node.
    private func step<C: View>(dot: Color, hollow: Bool = false, top: CGFloat, boxed: Bool, @ViewBuilder content: () -> C) -> some View {
        HStack(alignment: .top, spacing: 16) {
            Group {
                if hollow {
                    Circle().strokeBorder(dot, lineWidth: 1)
                } else {
                    Circle().fill(dot)
                }
            }
            .frame(width: 8, height: 8)
            .frame(width: 12)
            .padding(.top, top)
            .accessibilityHidden(true)
            Group {
                if boxed {
                    content()
                        .padding(16)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .dsCardSurface(fill: .clear, radius: DS.Radius.control, border: DS.Line.divider)
                } else {
                    content().frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        // The rail down to the next node, as tall as this one plus the gap.
        .background(alignment: .topLeading) {
            if !isLast {
                Rectangle()
                    .fill(DS.Line.divider)
                    .frame(width: 1)
                    .padding(.top, top + 16)
                    .padding(.bottom, -16)
                    .padding(.leading, 5.5)
                    .accessibilityHidden(true)
            }
        }
    }

    private var strategy: some View {
        let desc = event.desc ?? ""
        let parts = desc.contains(" → ") ? desc.components(separatedBy: " → ") : nil
        return step(dot: DS.Text.secondary, top: 20, boxed: true) {
            VStack(alignment: .leading, spacing: 0) {
                if let parts, parts.count >= 2 {
                    HStack(spacing: 8) {
                        Text(parts[0])
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.secondary)
                        BacktestPlaybackActionBadge(action: parts[1])
                    }
                    .padding(.bottom, 6)
                } else if !desc.isEmpty {
                    Text(desc)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .padding(.bottom, 6)
                }
                Text(event.name ?? "")
                    .font(DS.Font.bodyMedium)
                    .foregroundStyle(DS.Text.primary)
                if let reason = event.reason, !reason.isEmpty {
                    Text("\(Text("Reason").font(DS.Font.caption).foregroundStyle(DS.Text.muted))  \(reason)")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 8)
                }
                if let decision = event.decision, !decision.isEmpty {
                    Text("→ \(decision)")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.soft)
                        .padding(.top, 8)
                }
                if !event.tickers.isEmpty {
                    FlowLayout(spacing: 8, runSpacing: 4) {
                        Text("Scanning")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                        ForEach(Array(event.tickers.enumerated()), id: \.offset) { _, t in
                            Text(t)
                                .font(DS.Font.body)
                                .foregroundStyle(DS.Text.label)
                        }
                    }
                    .padding(.top, 8)
                }
            }
        }
    }

    private var outcome: some View {
        step(dot: DS.Text.muted, hollow: true, top: 6, boxed: false) {
            VStack(alignment: .leading, spacing: 4) {
                Text(event.name ?? "")
                    .font(DS.Font.captionMedium)
                    .foregroundStyle(DS.Text.label)
                if let details = event.details {
                    Text(details)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private var decision: some View {
        step(dot: DS.Text.soft, top: 20, boxed: true) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Execution decisions")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                if event.buys.isEmpty && event.sells.isEmpty {
                    Text("No actions taken")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.muted)
                } else {
                    ForEach(Array(event.buys.enumerated()), id: \.offset) { _, b in tradeRow(b, isBuy: true) }
                    ForEach(Array(event.sells.enumerated()), id: \.offset) { _, s in tradeRow(s, isBuy: false) }
                }
            }
        }
    }

    private func tradeRow(_ t: PlaybackTrade, isBuy: Bool) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 8) {
                BacktestDotWord(word: isBuy ? "Buy" : "Sell", color: isBuy ? DS.Palette.gain : DS.Palette.loss)
                    .foregroundStyle(DS.Text.label)
                Text(t.ticker ?? "?")
                    .font(DS.Font.bodyMedium)
                    .foregroundStyle(DS.Text.primary)
                Text("\(t.qty.map { dartToStringAsFixed($0.double, 0) } ?? "?") shares @ \(fmtMoney(t.price?.double))")
                    .foregroundStyle(DS.Text.secondary)
                    .monospacedDigit()
                    .lineLimit(1)
            }
            .font(DS.Font.body)
            if let reason = t.reason, !reason.isEmpty {
                Text(reason)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .lineLimit(2)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// The strategy node's action: a dot (gain, loss, or zinc-600 for hold) and
/// the word, sentence-cased.
struct BacktestPlaybackActionBadge: View {
    let action: String

    var body: some View {
        let c: Color = switch action.uppercased() {
        case "BUY": DS.Palette.gain
        case "SELL": DS.Palette.loss
        default: DS.Text.faint
        }
        BacktestDotWord(word: action.lowercased().dsSentenceCased, color: c)
            .font(DS.Font.caption)
            .foregroundStyle(DS.Text.soft)
    }
}
