import SwiftUI

/// The backtest replay (`/backtests/:id/playback`) — `BacktestPlaybackScreen`:
/// a portfolio panel over an execution log that grows frame by frame, with
/// play / reset / speed controls floating at the bottom.
struct BacktestPlaybackView: View {
    let id: String

    @Environment(AppServices.self) private var services
    @State private var model: BacktestPlaybackModel?

    var body: some View {
        Group {
            if let model {
                if model.loading {
                    BacktestPlaybackSkeleton()
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
                        MarketsTag(text: "LIVE", color: DS.Palette.warning, mono: true)
                    }
                    Text("Backtest #\(id)").font(.caption2).foregroundStyle(.secondary)
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
        VStack(spacing: 10) {
            HStack(spacing: 5) {
                Image(systemName: Symbol.named("calendar_today")).foregroundStyle(DS.Palette.info)
                Text(model.currentDateLabel).font(.caption.monospaced().weight(.semibold))
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(DS.Surface.panel, in: Capsule())
            .frame(maxWidth: .infinity, alignment: .trailing)
            .accessibilityLabel("Current date \(model.currentDateLabel)")

            portfolioPanel(model)
            eventLog(model)
        }
        .padding(.horizontal, 12)
        .padding(.top, 8)
        .safeAreaInset(edge: .bottom) { controls(model) }
    }

    private func controls(_ model: BacktestPlaybackModel) -> some View {
        GlassEffectContainer(spacing: 12) {
            HStack(spacing: 12) {
                Button {
                    model.togglePlay()
                } label: {
                    Image(systemName: Symbol.named(model.isPlaying ? "pause" : "play_arrow"))
                        .font(.title3)
                        .frame(width: 44, height: 44)
                }
                .dsGlassProminentButton()
                .tint(model.isPlaying ? DS.Palette.warning : DS.Palette.success)
                .accessibilityLabel(model.isPlaying ? "Pause" : "Play")
                Button {
                    model.reset()
                } label: {
                    Image(systemName: Symbol.named("replay"))
                        .font(.title3)
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.glass)
                .accessibilityLabel("Reset")
                Button {
                    model.cycleSpeed()
                } label: {
                    Text(model.speedLabel)
                        .font(.subheadline.monospaced().weight(.bold))
                        .frame(minWidth: 44, minHeight: 44)
                }
                .buttonStyle(.glass)
                .accessibilityLabel("Speed \(model.speedLabel)")
            }
        }
        .padding(.bottom, 8)
        .sensoryFeedback(.selection, trigger: model.speedIndex)
    }

    // MARK: Portfolio panel

    private func portfolioPanel(_ model: BacktestPlaybackModel) -> some View {
        let points = model.chartPoints()
        return Card(padding: 14) {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("PORTFOLIO VALUE").font(.footnote.weight(.semibold)).foregroundStyle(.secondary)
                        Text(fmtMoney(model.currentPortfolioValue.double))
                            .font(.title2.bold().monospacedDigit())
                            .contentTransition(.numericText(value: model.currentPortfolioValue.double))
                            .animation(.snappy, value: model.currentPortfolioValue.double)
                    }
                    Spacer(minLength: 8)
                    if !model.currentHoldings.isEmpty {
                        holdings(model.currentHoldings)
                    }
                }
                ScrubbableAreaChart(
                    timestamps: points.map(\.time),
                    values: points.map(\.value),
                    lineColor: DS.Palette.accent,
                    height: 170,
                    baseline: model.metadata.initialCash?.double,
                    // Live playback updates every tick — no entry animation.
                    animate: false
                )
            }
        }
    }

    private func holdings(_ holdings: [PlaybackHolding]) -> some View {
        MarketsFlowLayout(spacing: 4, runSpacing: 4, alignment: .trailing) {
            ForEach(Array(holdings.prefix(6).enumerated()), id: \.offset) { _, h in
                VStack(spacing: 0) {
                    Text(h.ticker).font(.caption2.monospaced().weight(.bold))
                    if let q = h.qty {
                        Text("\(dartToStringAsFixed(q.double, 0)) sh").font(.caption2).foregroundStyle(.secondary)
                    }
                    if let curr = h.curr, let avg = h.avg {
                        Text(fmtMoney(curr.double))
                            .font(.caption2)
                            .foregroundStyle(curr > avg ? DS.Palette.success : (curr < avg ? DS.Palette.danger : Color.secondary))
                    }
                }
                .padding(.horizontal, 6)
                .padding(.vertical, 3)
                .background(DS.Surface.inset, in: .rect(cornerRadius: 6, style: .continuous))
            }
        }
        .frame(maxWidth: 200)
    }

    // MARK: Event log

    private func eventLog(_ model: BacktestPlaybackModel) -> some View {
        let events = model.visibleEvents
        return VStack(alignment: .leading, spacing: 0) {
            Text("Execution Log")
                .font(.subheadline.weight(.semibold))
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
            Divider()
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        ForEach(Array(events.enumerated()), id: \.offset) { i, ev in
                            BacktestPlaybackEventNode(event: ev).id(i)
                        }
                        if model.isPlaying {
                            typingNode.id(-2)
                        }
                        Color.clear.frame(height: 1).id(-1)
                    }
                    .padding(12)
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
        HStack(spacing: 8) {
            Image(systemName: Symbol.named("more_horiz"))
                .font(.caption)
                .foregroundStyle(.tertiary)
                .frame(width: 30, height: 30)
                .background(Color(uiColor: .systemFill), in: Circle())
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(DS.Surface.inset)
                .frame(width: 80, height: 28)
        }
        .accessibilityLabel("Playing")
    }
}

/// One execution-log node (`_EventNode`): date marker, strategy, outcome or
/// decision; portfolio events are hidden.
struct BacktestPlaybackEventNode: View {
    let event: PlaybackEvent

    private static let indigo = Color(red: 0x81 / 255, green: 0x8C / 255, blue: 0xF8 / 255)

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
                .font(.caption2.weight(.bold))
                .foregroundStyle(.tint)
                .padding(.horizontal, 10)
                .padding(.vertical, 4)
                .background(DS.Palette.accent.opacity(DS.tintFill), in: Capsule())
            VStack { Divider() }
        }
        .padding(.vertical, 8)
    }

    private func step<C: View>(_ icon: String, _ color: Color, @ViewBuilder content: () -> C) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: Symbol.named(icon))
                .font(.caption)
                .foregroundStyle(color)
                .frame(width: 30, height: 30)
                .background(color.opacity(DS.tintFill), in: Circle())
            content().frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var strategy: some View {
        let desc = event.desc ?? ""
        let parts = desc.contains(" → ") ? desc.components(separatedBy: " → ") : nil
        return step("psychology", Self.indigo) {
            VStack(alignment: .leading, spacing: 4) {
                if let parts, parts.count >= 2 {
                    HStack(spacing: 6) {
                        Text(parts[0]).font(.caption.monospaced().weight(.bold))
                        BacktestPlaybackActionBadge(action: parts[1])
                    }
                } else {
                    Text(desc.uppercased()).font(.caption2.weight(.bold)).tracking(0.5).foregroundStyle(Self.indigo)
                }
                Text(event.name ?? "").font(.subheadline.weight(.semibold))
                if let reason = event.reason, !reason.isEmpty {
                    Text(reason).font(.footnote).foregroundStyle(.secondary)
                }
                if !event.tickers.isEmpty {
                    MarketsFlowLayout(spacing: 4, runSpacing: 4) {
                        Text("Scanning:").font(.caption2.weight(.bold)).foregroundStyle(.tertiary)
                        ForEach(Array(event.tickers.enumerated()), id: \.offset) { _, t in MarketsChip(text: t, color: .secondary) }
                    }
                    .padding(.top, 2)
                }
            }
            .padding(12)
            .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
        }
    }

    private var outcome: some View {
        step("score", DS.Palette.info) {
            VStack(alignment: .leading, spacing: 2) {
                Text(event.name ?? "").font(.subheadline.weight(.semibold)).foregroundStyle(DS.Palette.info)
                if let details = event.details {
                    Text(details).font(.caption2.monospaced()).foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(DS.Palette.info.opacity(DS.tintFill), in: .rect(cornerRadius: 10, style: .continuous))
        }
    }

    private var decision: some View {
        step("gavel", DS.Palette.warning) {
            VStack(alignment: .leading, spacing: 4) {
                Text("EXECUTION DECISIONS").font(.caption2.weight(.bold)).tracking(0.5).foregroundStyle(DS.Palette.warning)
                    .padding(.bottom, 4)
                if event.buys.isEmpty && event.sells.isEmpty {
                    Text("No actions taken.").font(.footnote).foregroundStyle(.secondary)
                } else {
                    ForEach(Array(event.buys.enumerated()), id: \.offset) { _, b in tradeRow(b, isBuy: true) }
                    ForEach(Array(event.sells.enumerated()), id: \.offset) { _, s in tradeRow(s, isBuy: false) }
                }
            }
            .padding(12)
            .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
        }
    }

    private func tradeRow(_ t: PlaybackTrade, isBuy: Bool) -> some View {
        let c = isBuy ? DS.Palette.success : DS.Palette.danger
        return HStack(spacing: 8) {
            MarketsTag(text: isBuy ? "Buy" : "Sell", color: c)
            Text(t.ticker ?? "?").font(.footnote.monospaced().weight(.bold))
            Text("\(t.qty.map { dartToStringAsFixed($0.double, 0) } ?? "?") @ \(fmtMoney(t.price?.double))")
                .font(.footnote)
                .foregroundStyle(.secondary)
            Spacer(minLength: 4)
            if let reason = t.reason {
                Text(reason).font(.caption2).foregroundStyle(.tertiary).lineLimit(1)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(c.opacity(0.1), in: .rect(cornerRadius: 8, style: .continuous))
    }
}

/// The strategy node's action tag (`_ActionBadge` in the playback screen).
struct BacktestPlaybackActionBadge: View {
    let action: String

    var body: some View {
        let c: Color = switch action.uppercased() {
        case "BUY": DS.Palette.success
        case "SELL": DS.Palette.danger
        default: .secondary
        }
        MarketsTag(text: action.uppercased(), color: c)
    }
}

/// The loading placeholder (`_PlaybackSkeleton`).
struct BacktestPlaybackSkeleton: View {
    var body: some View {
        VStack(spacing: 10) {
            Skeleton(height: 220, radius: DS.Radius.card)
            VStack(alignment: .leading, spacing: 10) {
                Skeleton.line(width: 100, height: 14)
                ForEach(0..<5, id: \.self) { i in
                    HStack(alignment: .top, spacing: 8) {
                        Skeleton.circle(30)
                        Skeleton(height: 44 + CGFloat(i % 2) * 14, radius: 10)
                    }
                }
            }
            .padding(12)
            .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.card, style: .continuous))
            Spacer()
        }
        .padding(12)
        .accessibilityLabel("Loading")
    }
}
