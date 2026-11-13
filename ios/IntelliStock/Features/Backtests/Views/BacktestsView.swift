import SwiftUI

/// All backtests (`/backtests`) — the web's `BacktestsView.vue`: "Backtests"
/// with "48 across all instances" under it, then one card of rows with live
/// status, and the page footer. A row opens the backtest; Pause, Resume and
/// Stop are swipe actions and context-menu items (still confirmed); per page
/// and the page jump sit in the toolbar menu, Previous / Next under the card.
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
        .background(DS.Surface.page)
        .navigationTitle("Backtests")
        .navigationBarTitleDisplayMode(.large)
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

    /// Per page and the page jump, as checkmarked submenus.
    private func pageMenu(_ model: BacktestsListModel) -> some View {
        ToolbarMenu("Page options") {
            Picker("Per page", selection: Binding(
                get: { model.perPage },
                set: { n in Task { await model.setPerPage(n) } }
            )) {
                ForEach(BacktestsListModel.perPageOptions, id: \.self) { Text("\($0) per page").tag($0) }
            }
            .pickerStyle(.menu)
            if model.totalPages > 1 {
                Picker("Go to page", selection: Binding(
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
            Section {
                Group {
                    if model.loading && model.rows.isEmpty {
                        Skeleton(width: 160, height: 14)
                    } else {
                        Text("\(model.total) across all instances")
                            .font(DS.Font.pageSub)
                            .foregroundStyle(DS.Text.muted)
                            .monospacedDigit()
                    }
                }
                .dsClearRow(top: 0, bottom: 4)
            }
            if model.loading && model.rows.isEmpty {
                DSSection {
                    ForEach(0..<6, id: \.self) { _ in skeletonRow }
                }
            } else if model.rows.isEmpty, let error = model.error {
                Section {
                    ErrorRow(message: error, onRetry: { Task { await model.refresh() } })
                        .dsClearRow()
                }
            } else if model.rows.isEmpty {
                DSSection {
                    EmptyState(title: "No backtests found", subtitle: "Run a backtest to see results here")
                }
            } else {
                DSSection(rowMinHeight: 68) {
                    ForEach(model.rows) { bt in
                        row(model, bt).id(bt.id)
                    }
                }
                pagination(model)
            }
        }
        .dsList()
        .refreshable { await model.refresh() }
    }

    // MARK: Row

    /// The loading shape (`ui-skel`): where the id, instance and P&L go.
    private var skeletonRow: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 8) {
                Skeleton(width: 72, height: 14)
                Skeleton(width: 168, height: 12)
            }
            Spacer(minLength: 8)
            Skeleton(width: 84, height: 14)
        }
        .padding(.vertical, 4)
        .accessibilityHidden(true)
    }

    private func row(_ model: BacktestsListModel, _ bt: BacktestRow) -> some View {
        let live = model.statusMap[bt.id]
        let status = live?.status ?? bt.status ?? "queued"
        let s = status.lowercased()
        let isActive = s == "running" || s == "queued" || s == "pending"
        let isPaused = s == "paused" || s == "paused_llm_critical"
        let canStop = isActive || isPaused
        let progress = live?.progress
        let lookback = live?.nexusLookback
        let elapsed = live?.timeElapsedSeconds ?? bt.timeElapsedSeconds
        let rowBusy = busy.contains(bt.id)

        return NavigationLink(value: Route.backtest(bt.id)) {
            BacktestListRow(
                bt: bt,
                status: status,
                pulsing: s == "running",
                elapsed: elapsed?.double,
                progress: isActive ? progress?.double : nil,
                lookback: lookback
            )
        }
        // Every swipe opens the confirmation, so none is a destructive role
        // (which would animate the row away before the answer).
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            if canStop {
                Button {
                    ask(model, bt, "stop")
                } label: {
                    Label("Stop", systemImage: Symbol.named("stop_circle"))
                }
                .tint(DS.Swipe.destructive)
                .disabled(rowBusy)
            }
            if isActive {
                Button {
                    ask(model, bt, "pause")
                } label: {
                    Label("Pause", systemImage: Symbol.named("pause_circle"))
                }
                .tint(DS.Swipe.neutral)
                .disabled(rowBusy)
            }
            if isPaused {
                Button {
                    ask(model, bt, "resume")
                } label: {
                    Label("Resume", systemImage: Symbol.named("play_circle"))
                }
                .tint(DS.Swipe.neutral)
                .disabled(rowBusy)
            }
        }
        .contextMenu {
            if let instanceId = bt.instanceId {
                Button {
                    services.router.push(.instance(instanceId))
                } label: {
                    Label("View instance", systemImage: Symbol.named("smart_toy"))
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
            title: "\(meta.verb) backtest",
            body: "\(meta.body)\n\nBacktest #\(bt.id)",
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

    /// The web's card footer: "Page 1 of 4 · 48 total" with Previous / Next;
    /// the page jump is in the toolbar menu.
    private func pagination(_ model: BacktestsListModel) -> some View {
        let page = model.page
        let loading = model.loading
        return Section {
            HStack(spacing: 4) {
                Text(model.totalPages > 1 ? "Page \(page) of \(model.totalPages) · \(model.total) total" : "\(model.total) total")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
                Spacer(minLength: 8)
                if model.totalPages > 1 {
                    pageNav("chevron_left", "Previous page", enabled: page > 1 && !loading) { Task { await model.goToPage(page - 1) } }
                    pageNav("chevron_right", "Next page", enabled: page < model.totalPages && !loading) { Task { await model.goToPage(page + 1) } }
                }
            }
            .frame(minHeight: 44)
            .dsClearRow(top: 4, bottom: 16, horizontal: DS.pageGutter + 4)
        }
    }

    private func pageNav(_ icon: String, _ label: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: Symbol.named(icon))
        }
        .buttonStyle(.dsIcon(side: 44))
        .disabled(!enabled)
        .accessibilityLabel(label)
    }
}

/// One backtest in the list, in the web's column order: `#id` and the P&L,
/// the instance and the P&L %, the stocks and the period, then the status
/// (a dot and a word), the elapsed time and when it ended. While it runs,
/// its progress and any Nexus lookback show as bars.
struct BacktestListRow: View {
    let bt: BacktestRow
    let status: String
    var pulsing = false
    let elapsed: Double?
    let progress: Double?
    let lookback: NexusLookback?

    var body: some View {
        let pnl = bt.pnl?.double
        let pct = bt.pnlPercent?.double
        var meta: [String] = []
        if elapsed != nil { meta.append(fmtElapsed(elapsed)) }
        // When it ended; the status word before it already says how.
        if let done = BacktestRowFormat.completedStamp(bt.completedAt) { meta.append(done) }
        return VStack(alignment: .leading, spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("#\(bt.id)")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.primary)
                        .lineLimit(1)
                    Spacer(minLength: 8)
                    Text(fmtPnl(pnl))
                        .font(DS.Font.body)
                        .foregroundStyle(backtestPnlColor(pnl))
                        .lineLimit(1)
                        .layoutPriority(1)
                }
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(bt.instanceId ?? "—")
                        .foregroundStyle(bt.instanceId == nil ? DS.Text.faint : DS.Text.muted)
                        .lineLimit(1)
                    Spacer(minLength: 8)
                    if pct != nil {
                        Text(fmtPct(pct))
                            .foregroundStyle(backtestPnlColor(pct))
                            .lineLimit(1)
                            .layoutPriority(1)
                    }
                }
                .font(DS.Font.caption)
                Text(BacktestStocksText.line(bt.stocks, start: bt.startDate, end: bt.endDate))
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .lineLimit(1)
            }
            .monospacedDigit()
            HStack(spacing: 6) {
                StatusDot(BacktestStatusText.label(status), status: status, pulsing: pulsing, font: DS.Font.caption)
                if !meta.isEmpty {
                    Text("·").foregroundStyle(DS.Text.faint)
                    Text(meta.joined(separator: " · "))
                        .foregroundStyle(DS.Text.muted)
                        .lineLimit(1)
                }
            }
            .font(DS.Font.caption)
            .monospacedDigit()
            if let progress {
                BacktestProgressLine(fraction: progress / 100, label: "\(Int(progress.rounded()))%")
            }
            if let lookback {
                BacktestProgressLine(fraction: lookback.fraction, label: "Lookback \(lookback.current)/\(lookback.total)d", fill: DS.Text.muted)
            }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Shared pieces (list, detail, playback)

/// The web's status words (`STATUS_UI`): "Running", "Paused · LLM failure",
/// "Finished", …; any other status sentence-cased with spaces for
/// underscores, "Unknown" when empty.
nonisolated enum BacktestStatusText {
    static func label(_ status: String?) -> String {
        let raw = status ?? ""
        switch raw.lowercased() {
        case "running": return "Running"
        case "queued": return "Queued"
        case "pending": return "Pending"
        case "paused": return "Paused"
        case "paused_llm_critical": return "Paused · LLM failure"
        case "completed": return "Completed"
        case "finished": return "Finished"
        case "stopped": return "Stopped"
        case "cancelled": return "Cancelled"
        case "error": return "Error"
        case "failed": return "Failed"
        default:
            let label = raw.replacingOccurrences(of: "_", with: " ")
            return label.isEmpty ? "Unknown" : label.dsSentenceCased
        }
    }
}

/// "QQQ, GLD, XLE, TQQQ +2 · Jan 2 – Dec 31, 2024": the first four stocks,
/// how many more, and the period.
nonisolated enum BacktestStocksText {
    static func stocks(_ stocks: [String]) -> String? {
        guard !stocks.isEmpty else { return nil }
        let head = stocks.prefix(4).joined(separator: ", ")
        return stocks.count > 4 ? "\(head) +\(stocks.count - 4)" : head
    }

    static func line(_ stocks: [String], start: String?, end: String?) -> String {
        [Self.stocks(stocks), BacktestRowFormat.dateRange(start, end)].compactMap { $0 }.joined(separator: " · ")
    }
}

/// The web's `pnlClass`: gain or loss by sign, zinc-500 when there is no
/// figure.
func backtestPnlColor(_ v: Double?) -> Color {
    guard let v else { return DS.Text.muted }
    return v >= 0 ? DS.Palette.gain : DS.Palette.loss
}

/// A thin progress bar (the web's `bar-track` + `bar-fill`): a 4 pt raised
/// track with a zinc fill, eased when it moves.
struct BacktestProgressBar: View {
    let fraction: Double
    var fill: Color = DS.Text.label

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(DS.Surface.raised)
                Capsule()
                    .fill(fill)
                    .frame(width: geo.size.width * min(max(fraction, 0), 1))
            }
        }
        .frame(height: 4)
        .animation(reduceMotion ? nil : DSMotion.ease(0.5), value: fraction)
        .accessibilityHidden(true)
    }
}

/// A progress bar with its figure beside it ("62%", "Lookback 41/90d").
struct BacktestProgressLine: View {
    let fraction: Double
    let label: String
    var fill: Color = DS.Text.label

    var body: some View {
        HStack(spacing: 8) {
            BacktestProgressBar(fraction: fraction, fill: fill)
                .frame(maxWidth: 120)
            Text(label)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .monospacedDigit()
                .lineLimit(1)
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .ignore)
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

    /// When a run completed, compact for a row: "Nov 12, 1:32 PM" this
    /// year, "Dec 30, 2025" before it; nil when it has not.
    static func completedStamp(_ v: JSON, now: Date = Date()) -> String? {
        guard !v.isNull, let date = parseDateTime(v) else { return nil }
        let local = Calendar.current
        if local.component(.year, from: date) == local.component(.year, from: now) {
            return localMonthDayTime.string(from: date)
        }
        return localFull.string(from: date)
    }

    private static func localFormatter(_ pattern: String) -> DateFormatter {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = .autoupdatingCurrent
        f.dateFormat = pattern
        return f
    }

    private static let localMonthDayTime = localFormatter("MMM d, h:mm a")
    private static let localFull = localFormatter("MMM d, yyyy")

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
