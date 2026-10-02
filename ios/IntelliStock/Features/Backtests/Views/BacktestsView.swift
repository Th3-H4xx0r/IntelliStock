import SwiftUI

/// All backtests (`/backtests`) — `BacktestsScreen`: a page of backtest
/// cards with live status, pause / resume / stop, and pagination.
struct BacktestsView: View {
    @Environment(AppServices.self) private var services
    @State private var model: BacktestsListModel?
    @State private var confirm: ConfirmRequest?
    @State private var toast: Toast?
    /// Rows with a confirmed action in flight (no double submit).
    @State private var busy: Set<String> = []

    var body: some View {
        Group {
            if let model {
                content(model)
            } else {
                Color.clear
            }
        }
        .background(DS.Surface.canvas)
        .navigationTitle("All Backtests")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                if model?.loading == true {
                    ProgressView()
                } else {
                    Button {
                        Task { await model?.refresh() }
                    } label: {
                        Label("Refresh", systemImage: Symbol.named("refresh"))
                    }
                }
            }
        }
        .task {
            let m = model ?? BacktestsListModel(repository: { [services] in services.backtestRepository })
            model = m
            await m.run(lifecycle: services.lifecycle)
        }
        .confirmAlert($confirm)
        .toast($toast)
    }

    private func content(_ model: BacktestsListModel) -> some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Backtests (\(model.total))")
                            .font(.footnote.weight(.semibold))
                            .foregroundStyle(.secondary)
                        Text("\(model.total) total")
                            .font(.caption)
                            .foregroundStyle(.tertiary)
                    }
                    Spacer()
                    Text("Per page").font(.footnote).foregroundStyle(.secondary)
                    Picker("Per page", selection: Binding(
                        get: { model.perPage },
                        set: { n in Task { await model.setPerPage(n) } }
                    )) {
                        ForEach(BacktestsListModel.perPageOptions, id: \.self) { Text("\($0)").tag($0) }
                    }
                    .pickerStyle(.menu)
                    .labelsHidden()
                }
                .padding(.top, 8)

                if model.loading && model.rows.isEmpty {
                    ForEach(0..<5, id: \.self) { _ in skeletonCard }
                } else if model.rows.isEmpty, let error = model.error {
                    ErrorRow(message: error, onRetry: { Task { await model.refresh() } })
                } else if model.rows.isEmpty {
                    EmptyState(
                        systemImage: Symbol.named("analytics"),
                        title: "No backtests found",
                        subtitle: "Run a backtest to see results here."
                    )
                    .padding(.top, 40)
                } else {
                    ForEach(model.rows) { bt in
                        card(model, bt).id(bt.id)
                    }
                    pagination(model)
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 24)
        }
        .refreshable { await model.refresh() }
    }

    // MARK: Card

    private var skeletonCard: some View {
        Card(padding: 16) {
            VStack(alignment: .leading, spacing: 10) {
                HStack { Text("instance-id-placeholder"); Spacer(); Text("RUNNING") }
                Text("AAPL  MSFT  NVDA")
                Text("2026-01-01 → 2026-02-01")
                Text("+$1,234.56  +1.23%").font(.title3)
            }
        }
        .redacted(reason: .placeholder)
    }

    private func card(_ model: BacktestsListModel, _ bt: BacktestRow) -> some View {
        let live = model.statusMap[bt.id]
        let status = live?.status ?? bt.status ?? "queued"
        let s = status.lowercased()
        let isActive = s == "running" || s == "queued" || s == "pending"
        let isPaused = s == "paused" || s == "paused_llm_critical"
        let canStop = isActive || isPaused
        let progress = live?.progress
        let lookback = live?.nexusLookback
        let elapsed = live?.timeElapsedSeconds ?? bt.timeElapsedSeconds
        let pnlC = pnlColor(bt.pnl?.double)
        let rowBusy = busy.contains(bt.id)

        return Button {
            services.router.push(.backtest(bt.id))
        } label: {
            Card(padding: 16) {
                VStack(alignment: .leading, spacing: 0) {
                    HStack(alignment: .top, spacing: 8) {
                        VStack(alignment: .leading, spacing: 2) {
                            if let instanceId = bt.instanceId {
                                Button {
                                    services.router.push(.instance(instanceId))
                                } label: {
                                    Text(instanceId)
                                        .font(.subheadline.weight(.semibold))
                                        .foregroundStyle(.tint)
                                        .lineLimit(1)
                                }
                                .buttonStyle(.plain)
                            } else {
                                Text("Backtest").font(.subheadline.weight(.semibold))
                            }
                            Text("#\(bt.id)")
                                .font(.caption.monospaced())
                                .foregroundStyle(.tertiary)
                                .lineLimit(1)
                        }
                        Spacer(minLength: 8)
                        StatusBadge(label: status.uppercased(), color: StatusBadge.color(forStatus: status), pulsing: isActive)
                    }
                    if !bt.stocks.isEmpty {
                        MarketsFlowLayout {
                            ForEach(Array(bt.stocks.prefix(4).enumerated()), id: \.offset) { _, s in MarketsChip(text: s) }
                            if bt.stocks.count > 4 {
                                Text("+\(bt.stocks.count - 4)").font(.footnote).foregroundStyle(.tertiary)
                            }
                        }
                        .padding(.top, 12)
                    }
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(bt.pnl != nil ? fmtPnl(bt.pnl?.double) : "—")
                            .font(.title3.weight(.bold).monospacedDigit())
                            .foregroundStyle(pnlC)
                            .lineLimit(1)
                        if bt.pnlPercent != nil {
                            Text(fmtPct(bt.pnlPercent?.double))
                                .font(.footnote.weight(.semibold).monospacedDigit())
                                .foregroundStyle(pnlC)
                        }
                        Spacer(minLength: 8)
                        Image(systemName: Symbol.named("arrow_forward"))
                            .font(.footnote.weight(.semibold))
                            .foregroundStyle(.tint)
                            .frame(width: 32, height: 32)
                            .background(DS.Palette.accent.opacity(DS.tintFill), in: .rect(cornerRadius: 8, style: .continuous))
                    }
                    .padding(.top, 14)
                    Divider().padding(.vertical, 10)
                    HStack(spacing: 4) {
                        Text("\(bt.startDate ?? "?") → \(bt.endDate ?? "?")")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                        Spacer(minLength: 8)
                        Image(systemName: "timer").font(.caption2).foregroundStyle(.tertiary)
                        Text(fmtElapsed(elapsed?.double))
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                    }
                    if !bt.completedAt.isNull {
                        Text("Completed \(fmtDateTime(bt.completedAt))")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                            .padding(.top, 4)
                    }
                    if isActive, let progress {
                        HStack(spacing: 8) {
                            ProgressView(value: min(max(progress.double / 100, 0), 1))
                                .tint(DS.Palette.info)
                            Text("\(Int(progress.double.rounded()))%")
                                .font(.caption2.monospaced())
                                .foregroundStyle(.secondary)
                        }
                        .padding(.top, 10)
                    }
                    if let lookback {
                        VStack(spacing: 4) {
                            HStack(spacing: 4) {
                                Image(systemName: Symbol.named("hub")).font(.caption2).foregroundStyle(.tint)
                                Text("Lookback").font(.caption2.weight(.semibold)).foregroundStyle(.tint)
                                Spacer()
                                Text("\(lookback.current)/\(lookback.total)d")
                                    .font(.caption2.monospaced())
                                    .foregroundStyle(.secondary)
                            }
                            ProgressView(value: min(max(lookback.fraction, 0), 1))
                                .tint(DS.Palette.accent.opacity(0.7))
                        }
                        .padding(.top, 10)
                    }
                    if canStop {
                        HStack(spacing: 4) {
                            Spacer()
                            if isActive {
                                actionButton("pause_circle", "Pause", DS.Palette.accent, disabled: rowBusy) { ask(model, bt, "pause") }
                            }
                            if isPaused {
                                actionButton("play_circle", "Resume", DS.Palette.info, disabled: rowBusy) { ask(model, bt, "resume") }
                            }
                            actionButton("stop_circle", "Stop", DS.Palette.danger, disabled: rowBusy) { ask(model, bt, "stop") }
                        }
                        .padding(.top, 12)
                    }
                }
            }
        }
        .buttonStyle(.plain)
    }

    private func actionButton(_ icon: String, _ label: String, _ color: Color, disabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: Symbol.named(icon))
                .font(.title3)
                .foregroundStyle(color)
                .frame(width: 44, height: 44)
        }
        .buttonStyle(.borderless)
        .disabled(disabled)
        .accessibilityLabel(label)
    }

    private func ask(_ model: BacktestsListModel, _ bt: BacktestRow, _ action: String) {
        let meta = BacktestsListModel.actionMeta(action)
        confirm = ConfirmRequest(
            title: "\(meta.verb) Backtest",
            body: "\(meta.body)\n\nBacktest ID: \(bt.id)",
            confirmLabel: meta.verb,
            role: action == "stop" ? .destructive : nil,
            onConfirm: {
                busy.insert(bt.id)
                defer { busy.remove(bt.id) }
                if let err = await model.performAction(bt.id, action) {
                    throw ApiError(message: err)
                }
            },
            onError: { error in
                if !error.isCancellation { toast = Toast(KalshiFormat.errorText(error), style: .error) }
            }
        )
    }

    // MARK: Pagination

    @ViewBuilder
    private func pagination(_ model: BacktestsListModel) -> some View {
        if model.totalPages > 1 {
            let page = model.page
            let loading = model.loading
            VStack(spacing: 8) {
                Text("Page \(page) of \(model.totalPages)  (\(model.total) total)")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                HStack(spacing: 4) {
                    pageNav("chevron_left", "Previous page", enabled: page > 1 && !loading) { Task { await model.goToPage(page - 1) } }
                    ForEach(Array(BacktestsListModel.buildPages(current: page, total: model.totalPages).enumerated()), id: \.offset) { _, p in
                        if let p {
                            Button {
                                Task { await model.goToPage(p) }
                            } label: {
                                Text("\(p)")
                                    .font(.footnote.weight(p == page ? .semibold : .regular).monospacedDigit())
                                    .foregroundStyle(p == page ? AnyShapeStyle(.tint) : AnyShapeStyle(.secondary))
                                    .frame(minWidth: 32, minHeight: 32)
                                    .background(p == page ? DS.Palette.accent.opacity(DS.tintFill) : .clear, in: .rect(cornerRadius: 8, style: .continuous))
                            }
                            .buttonStyle(.plain)
                            .disabled(loading)
                            .accessibilityAddTraits(p == page ? .isSelected : [])
                        } else {
                            Text("…").font(.footnote).foregroundStyle(.tertiary)
                        }
                    }
                    pageNav("chevron_right", "Next page", enabled: page < model.totalPages && !loading) { Task { await model.goToPage(page + 1) } }
                }
            }
            .frame(maxWidth: .infinity)
            .padding(.top, 4)
        }
    }

    private func pageNav(_ icon: String, _ label: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: Symbol.named(icon))
                .frame(width: 32, height: 32)
        }
        .buttonStyle(.bordered)
        .disabled(!enabled)
        .accessibilityLabel(label)
    }
}
