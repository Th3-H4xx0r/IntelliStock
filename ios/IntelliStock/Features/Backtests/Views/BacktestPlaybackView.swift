import SwiftUI

/// The backtest replay (`/backtests/:id/playback`) — `BacktestPlaybackScreen`:
/// a portfolio panel over an execution log that grows frame by frame, with
/// the play / restart / speed transport as one glass control bar floating at
/// the bottom, above the tab bar's accessory.
struct BacktestPlaybackView: View {
    let id: String

    @Environment(AppServices.self) private var services
    @State private var model: BacktestPlaybackModel?

    var body: some View {
        Group {
            if let model {
                if model.loading {
                    ProgressView()
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let error = model.error {
                    message(icon: "error", tint: DS.Palette.danger, title: "Failed to load playback", body: error)
                } else if model.isEmpty {
                    message(icon: "play_disabled", tint: .secondary, title: "No playback data",
                            body: "This backtest has no portfolio snapshots or trades to replay.")
                } else {
                    playback(model)
                }
            } else {
                Color.clear
            }
        }
        .background(DS.Surface.canvas)
        .navigationTitle("Backtest Playback")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                VStack(spacing: 0) {
                    HStack(spacing: 6) {
                        Text("Backtest Playback").font(.headline)
                        MarketsTag(text: "Live", color: DS.Palette.warning)
                    }
                    Text("Backtest #\(id)").font(.caption).foregroundStyle(.secondary)
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

    private func message(icon: String, tint: Color, title: String, body: String) -> some View {
        ContentUnavailableView {
            Label(title, systemImage: Symbol.named(icon))
        } description: {
            Text(body)
        } actions: {
            Button(action: back) {
                Label("Back to Backtest", systemImage: Symbol.named("arrow_back"))
            }
        }
        .foregroundStyle(tint == .secondary ? Color.primary : tint)
    }

    // MARK: Body

    private func playback(_ model: BacktestPlaybackModel) -> some View {
        VStack(spacing: 12) {
            portfolioPanel(model)
            eventLog(model)
        }
        .padding(.horizontal, 16)
        .padding(.top, 8)
        .safeAreaInset(edge: .bottom) { controls(model) }
    }

    /// The transport: restart, play / pause and speed as one glass control
    /// bar, play the prominent (accent) glass.
    private func controls(_ model: BacktestPlaybackModel) -> some View {
        // One container groups the three; the gaps stay wider than its
        // spacing so the prominent play keeps its own accent glass (a
        // united shape would take one variant and lose the accent).
        GlassEffectContainer(spacing: 8) {
            HStack(spacing: 12) {
                Button {
                    model.reset()
                } label: {
                    Image(systemName: Symbol.named("replay"))
                        .font(.body.weight(.semibold))
                        .frame(width: 32, height: 32)
                }
                .buttonStyle(.glass)
                .accessibilityLabel("Reset")
                Button {
                    model.togglePlay()
                } label: {
                    Image(systemName: Symbol.named(model.isPlaying ? "pause" : "play_arrow"))
                        .font(.title3.weight(.semibold))
                        .frame(width: 40, height: 40)
                        .contentTransition(.symbolEffect(.replace))
                }
                .dsGlassProminentButton()
                .accessibilityLabel(model.isPlaying ? "Pause" : "Play")
                Button {
                    model.cycleSpeed()
                } label: {
                    Text(model.speedLabel)
                        .font(.subheadline.weight(.semibold).monospacedDigit())
                        .frame(minWidth: 32, minHeight: 32)
                }
                .buttonStyle(.glass)
                .accessibilityLabel("Speed \(model.speedLabel)")
            }
        }
        .padding(.bottom, 8)
        .sensoryFeedback(.selection, trigger: model.speedIndex)
    }

    // MARK: Portfolio panel

    /// The hero: the replayed portfolio value with the replay date as its
    /// status line, the holdings at that frame, then the chart.
    private func portfolioPanel(_ model: BacktestPlaybackModel) -> some View {
        let points = model.chartPoints()
        return Card {
            VStack(alignment: .leading, spacing: DS.cardGroupSpacing) {
                HeroValueHeader(
                    fmtMoney(model.currentPortfolioValue.double),
                    numericValue: model.currentPortfolioValue.double,
                    status: "Portfolio value · \(model.currentDateLabel)"
                )
                if !model.currentHoldings.isEmpty {
                    holdings(model.currentHoldings)
                }
                ScrubbableAreaChart(
                    timestamps: points.map(\.time),
                    values: points.map(\.value),
                    lineColor: DS.Palette.accent,
                    height: 160,
                    baseline: model.metadata.initialCash?.double,
                    // Live playback updates every tick — no entry animation.
                    animate: false
                )
            }
        }
    }

    /// The first six holdings as small neutral tags: ticker, shares, and the
    /// price in green or red against the average cost.
    private func holdings(_ holdings: [PlaybackHolding]) -> some View {
        MarketsFlowLayout(spacing: 6, runSpacing: 6) {
            ForEach(Array(holdings.prefix(6).enumerated()), id: \.offset) { _, h in
                HStack(spacing: 4) {
                    Text(h.ticker).font(.caption.weight(.semibold))
                    if let q = h.qty {
                        Text("\(dartToStringAsFixed(q.double, 0)) sh").font(.caption).foregroundStyle(.secondary)
                    }
                    if let curr = h.curr, let avg = h.avg {
                        Text(fmtMoney(curr.double))
                            .font(.caption)
                            .foregroundStyle(curr > avg ? DS.Palette.success : (curr < avg ? DS.Palette.danger : Color.secondary))
                    }
                }
                .monospacedDigit()
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background(Color(uiColor: .tertiarySystemFill), in: Capsule())
                .accessibilityElement(children: .combine)
            }
        }
    }

    // MARK: Event log

    private func eventLog(_ model: BacktestPlaybackModel) -> some View {
        let events = model.visibleEvents
        return VStack(alignment: .leading, spacing: 0) {
            Text("Execution Log")
                .font(.headline)
                .accessibilityAddTraits(.isHeader)
                .padding(.horizontal, 16)
                .padding(.top, 14)
                .padding(.bottom, 8)
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 12) {
                        ForEach(Array(events.enumerated()), id: \.offset) { i, ev in
                            BacktestPlaybackEventNode(event: ev).id(i)
                        }
                        if model.isPlaying {
                            typingNode.id(-2)
                        }
                        Color.clear.frame(height: 1).id(-1)
                    }
                    .padding(.horizontal, 16)
                    .padding(.bottom, 12)
                }
                .onChange(of: model.frameIndex) {
                    withAnimation(.easeOut(duration: 0.3)) { proxy.scrollTo(-1, anchor: .bottom) }
                }
            }
        }
        .frame(maxHeight: .infinity)
        .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.card, style: .continuous))
    }

    private var typingNode: some View {
        HStack(spacing: 10) {
            ProgressView()
                .frame(width: 28, height: 28)
            Text("…").foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Playing")
    }
}

/// One execution-log node (`_EventNode`): date marker, strategy, outcome or
/// decision; portfolio events are hidden. Each is a plain row (glyph and
/// text) inside the log panel, with no inner tile.
struct BacktestPlaybackEventNode: View {
    let event: PlaybackEvent

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
        HStack(spacing: 8) {
            VStack { Divider() }
            Text("\(event.label ?? "") — \(event.time ?? "")")
                .font(.caption.weight(.semibold).monospacedDigit())
                .foregroundStyle(.secondary)
                .fixedSize()
            VStack { Divider() }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }

    private func step<C: View>(_ icon: String, @ViewBuilder content: () -> C) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: Symbol.named(icon))
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .frame(width: 28, height: 28)
                .accessibilityHidden(true)
            content().frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var strategy: some View {
        let desc = event.desc ?? ""
        let parts = desc.contains(" → ") ? desc.components(separatedBy: " → ") : nil
        return step("psychology") {
            VStack(alignment: .leading, spacing: 4) {
                if let parts, parts.count >= 2 {
                    HStack(spacing: 6) {
                        Text(parts[0]).font(.subheadline.weight(.semibold))
                        BacktestPlaybackActionBadge(action: parts[1])
                    }
                } else {
                    Text(desc).font(.footnote).foregroundStyle(.secondary)
                }
                Text(event.name ?? "").font(.subheadline.weight(.semibold))
                if let reason = event.reason, !reason.isEmpty {
                    Text(reason).font(.footnote).foregroundStyle(.secondary)
                }
                if !event.tickers.isEmpty {
                    MarketsFlowLayout(spacing: 4, runSpacing: 4) {
                        Text("Scanning:").font(.caption).foregroundStyle(.secondary)
                        ForEach(Array(event.tickers.enumerated()), id: \.offset) { _, t in MarketsChip(text: t) }
                    }
                    .padding(.top, 2)
                }
            }
        }
    }

    private var outcome: some View {
        step("score") {
            VStack(alignment: .leading, spacing: 2) {
                Text(event.name ?? "").font(.subheadline.weight(.semibold))
                if let details = event.details {
                    Text(details).font(.caption.monospaced()).foregroundStyle(.secondary)
                }
            }
        }
    }

    private var decision: some View {
        step("gavel") {
            VStack(alignment: .leading, spacing: 6) {
                Text("Execution decisions").font(.subheadline.weight(.semibold))
                if event.buys.isEmpty && event.sells.isEmpty {
                    Text("No actions taken.").font(.footnote).foregroundStyle(.secondary)
                } else {
                    ForEach(Array(event.buys.enumerated()), id: \.offset) { _, b in tradeRow(b, isBuy: true) }
                    ForEach(Array(event.sells.enumerated()), id: \.offset) { _, s in tradeRow(s, isBuy: false) }
                }
            }
        }
    }

    private func tradeRow(_ t: PlaybackTrade, isBuy: Bool) -> some View {
        let c = isBuy ? DS.Palette.success : DS.Palette.danger
        return HStack(spacing: 8) {
            MarketsTag(text: isBuy ? "Buy" : "Sell", color: c)
            Text(t.ticker ?? "?").font(.footnote.weight(.semibold))
            Text("\(t.qty.map { dartToStringAsFixed($0.double, 0) } ?? "?") @ \(fmtMoney(t.price?.double))")
                .font(.footnote.monospacedDigit())
                .foregroundStyle(.secondary)
            Spacer(minLength: 4)
            if let reason = t.reason {
                Text(reason).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// The strategy node's action tag (`_ActionBadge` in the playback screen),
/// sentence-cased.
struct BacktestPlaybackActionBadge: View {
    let action: String

    var body: some View {
        let c: Color = switch action.uppercased() {
        case "BUY": DS.Palette.success
        case "SELL": DS.Palette.danger
        default: .secondary
        }
        MarketsTag(text: action.lowercased().dsSentenceCased, color: c)
    }
}
