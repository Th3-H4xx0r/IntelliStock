import SwiftUI

/// All backtests (`/backtests`) — `BacktestsScreen`: a page of backtests
/// with live status, pause / resume / stop, and pagination. An inset-grouped
/// list of `EntityRow`s: the row opens the backtest; Pause, Resume and Stop
/// are swipe actions and context-menu items (still confirmed); per page and
/// the page jump sit in the toolbar menu, with Previous / Next at the bottom.
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
            if let model {
                if model.loading, !model.rows.isEmpty {
                    ToolbarItem(placement: .topBarTrailing) { ProgressView() }
                }
                ToolbarItem(placement: .topBarTrailing) { pageMenu(model) }
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

    // MARK: Toolbar

    /// Per page and the page jump (`_PageButton`s), as checkmarked submenus.
    private func pageMenu(_ model: BacktestsListModel) -> some View {
        ToolbarMenu("Page Options") {
            Picker("Per Page", selection: Binding(
                get: { model.perPage },
                set: { n in Task { await model.setPerPage(n) } }
            )) {
                ForEach(BacktestsListModel.perPageOptions, id: \.self) { Text("\($0) per page").tag($0) }
            }
            .pickerStyle(.menu)
            if model.totalPages > 1 {
                Picker("Go to Page", selection: Binding(
                    get: { model.page },
                    set: { p in Task { await model.goToPage(p) } }
                )) {
                    ForEach(BacktestsListModel.buildPages(current: model.page, total: model.totalPages).compactMap { $0 }, id: \.self) { p in
                        Text("Page \(p)").tag(p)
                    }
                }
                .pickerStyle(.menu)
                .disabled(model.loading)
            }
        }
    }

    // MARK: Content

    private func content(_ model: BacktestsListModel) -> some View {
        List {
            if model.loading && model.rows.isEmpty {
                Section {
                    ForEach(0..<6, id: \.self) { _ in skeletonRow }
                }
            } else if model.rows.isEmpty, let error = model.error {
                Section {
                    ErrorRow(message: error, onRetry: { Task { await model.refresh() } })
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                }
            } else if !model.rows.isEmpty {
                Section {
                    ForEach(model.rows) { bt in
                        row(model, bt).id(bt.id)
                    }
                }
                pagination(model)
            }
        }
        .listStyle(.insetGrouped)
        .overlay {
            if !model.loading, model.rows.isEmpty, model.error == nil {
                EmptyState(
                    systemImage: Symbol.named("analytics"),
                    title: "No backtests found",
                    subtitle: "Run a backtest to see results here."
                )
            }
        }
        .refreshable { await model.refresh() }
    }

    // MARK: Row

    private var skeletonRow: some View {
        EntityRow("instance-id-placeholder", subtitle: "#000000 · Jan 1 – Feb 1, 2026") {
            EntityRowValue("+$1,234.56", detail: "+1.23%")
        }
        .redacted(reason: .placeholder)
    }

    private func row(_ model: BacktestsListModel, _ bt: BacktestRow) -> some View {
        let live = model.statusMap[bt.id]
        let status = live?.status ?? bt.status ?? "queued"
        let s = status.lowercased()
        let isActive = s == "running" || s == "queued" || s == "pending"
        let isPaused = s == "paused" || s == "paused_llm_critical"
        let canStop = isActive || isPaused
        let finished = s == "finished" || s == "completed"
        let progress = live?.progress
        let lookback = live?.nexusLookback
        let elapsed = live?.timeElapsedSeconds ?? bt.timeElapsedSeconds
        let pnlC = pnlColor(bt.pnl?.double)
        let rowBusy = busy.contains(bt.id)
        let meta = [fmtElapsed(elapsed?.double), bt.completedAt.isNull ? nil : "Completed \(fmtDateTime(bt.completedAt))"]
            .compactMap { $0 }
            .joined(separator: " · ")

        return NavigationLink(value: Route.backtest(bt.id)) {
            VStack(alignment: .leading, spacing: 8) {
                // Stocks-style two columns: the name over "#id · dates" on
                // the left, the P&L over its % on the right, line by line, so
                // the dates get the width the narrower % leaves.
                VStack(alignment: .leading, spacing: 2) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(bt.instanceId ?? "Backtest")
                            .font(.headline)
                            .lineLimit(1)
                        Spacer(minLength: 8)
                        Text(bt.pnl != nil ? fmtPnl(bt.pnl?.double) : "—")
                            .monospacedDigit()
                            .foregroundStyle(pnlC)
                            .lineLimit(1)
                            .layoutPriority(1)
                    }
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text("#\(bt.id) · \(BacktestRowFormat.dateRange(bt.startDate, bt.endDate))")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                        Spacer(minLength: 8)
                        if bt.pnlPercent != nil {
                            Text(fmtPct(bt.pnlPercent?.double))
                                .font(.footnote.monospacedDigit())
                                .foregroundStyle(pnlC)
                                .lineLimit(1)
                                .layoutPriority(1)
                        }
                    }
                }
                .accessibilityElement(children: .combine)
                if !meta.isEmpty || !finished {
                    HStack(spacing: 8) {
                        if !meta.isEmpty {
                            Text(meta)
                                .font(.footnote.monospacedDigit())
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                        if !finished {
                            Spacer(minLength: 4)
                            StatusDot(status.dsSentenceCased, status: status, pulsing: isActive, font: .footnote)
                        }
                    }
                }
                if isActive, let progress {
                    HStack(spacing: 8) {
                        ProgressView(value: min(max(progress.double / 100, 0), 1))
                        Text("\(Int(progress.double.rounded()))%")
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                }
                if let lookback {
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text("Lookback").font(.caption).foregroundStyle(.secondary)
                            Spacer()
                            Text("\(lookback.current)/\(lookback.total)d")
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                        ProgressView(value: min(max(lookback.fraction, 0), 1))
                    }
                }
            }
        }
        // Every swipe opens the Dart confirmation, so none is a destructive
        // role (which would animate the row away before the answer).
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            if canStop {
                Button {
                    ask(model, bt, "stop")
                } label: {
                    Label("Stop", systemImage: Symbol.named("stop_circle"))
                }
                .tint(DS.Palette.danger)
                .disabled(rowBusy)
            }
            if isActive {
                Button {
                    ask(model, bt, "pause")
                } label: {
                    Label("Pause", systemImage: Symbol.named("pause_circle"))
                }
                .tint(DS.Palette.accent)
                .disabled(rowBusy)
            }
            if isPaused {
                Button {
                    ask(model, bt, "resume")
                } label: {
                    Label("Resume", systemImage: Symbol.named("play_circle"))
                }
                .tint(DS.Palette.info)
                .disabled(rowBusy)
            }
        }
        .contextMenu {
            if let instanceId = bt.instanceId {
                Button {
                    services.router.push(.instance(instanceId))
                } label: {
                    Label("View Instance", systemImage: Symbol.named("smart_toy"))
                }
            }
            if canStop {
                Section {
                    if isActive {
                        Button {
                            ask(model, bt, "pause")
                        } label: {
                            Label("Pause", systemImage: Symbol.named("pause_circle"))
                        }
                    }
                    if isPaused {
                        Button {
                            ask(model, bt, "resume")
                        } label: {
                            Label("Resume", systemImage: Symbol.named("play_circle"))
                        }
                    }
                    Button(role: .destructive) {
                        ask(model, bt, "stop")
                    } label: {
                        Label("Stop", systemImage: Symbol.named("stop_circle"))
                    }
                }
                .disabled(rowBusy)
            }
        }
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

    /// Previous / Next with "Page n of m (t total)" between them; the page
    /// jump is in the toolbar menu.
    @ViewBuilder
    private func pagination(_ model: BacktestsListModel) -> some View {
        let page = model.page
        let loading = model.loading
        if model.totalPages > 1 {
            Section {
                HStack {
                    pageNav("chevron_left", "Previous page", enabled: page > 1 && !loading) { Task { await model.goToPage(page - 1) } }
                    Spacer()
                    Text("Page \(page) of \(model.totalPages)  (\(model.total) total)")
                        .font(.footnote.monospacedDigit())
                        .foregroundStyle(.secondary)
                    Spacer()
                    pageNav("chevron_right", "Next page", enabled: page < model.totalPages && !loading) { Task { await model.goToPage(page + 1) } }
                }
            }
        } else {
            Section {
            } footer: {
                Text("\(model.total) total")
            }
        }
    }

    private func pageNav(_ icon: String, _ label: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: Symbol.named(icon))
                .font(.body.weight(.semibold))
                .frame(width: 44, height: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.borderless)
        .disabled(!enabled)
        .accessibilityLabel(label)
    }
}

/// Row text for a backtest.
nonisolated enum BacktestRowFormat {
    /// "Jul 7 – Sep 18, 2026" for two `yyyy-MM-dd` dates in one year,
    /// "Dec 1, 2025 – Feb 1, 2026" across years, and the raw "start → end"
    /// (with "?" for a missing end) when either does not parse.
    static func dateRange(_ start: String?, _ end: String?) -> String {
        guard let s = start.flatMap(parse), let e = end.flatMap(parse) else {
            return "\(start ?? "?") → \(end ?? "?")"
        }
        let cal = calendar
        if cal.component(.year, from: s) == cal.component(.year, from: e) {
            return "\(monthDay.string(from: s)) – \(full.string(from: e))"
        }
        return "\(full.string(from: s)) – \(full.string(from: e))"
    }

    private static let calendar: Calendar = {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        return cal
    }()

    private static func formatter(_ pattern: String) -> DateFormatter {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = calendar
        f.timeZone = calendar.timeZone
        f.dateFormat = pattern
        return f
    }

    private static let iso = formatter("yyyy-MM-dd")
    private static let monthDay = formatter("MMM d")
    private static let full = formatter("MMM d, yyyy")

    private static func parse(_ text: String) -> Date? {
        iso.date(from: String(text.prefix(10)))
    }
}
