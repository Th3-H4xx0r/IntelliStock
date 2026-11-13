import SwiftUI

/// One strategy (`/strategies/:id`) — the web's `StrategyDetailView.vue`:
/// "Strategy ID 3 · 2 sub-strategies · 5 backtests" under the inline name,
/// the best backtest's P&L and P&L % with "View best backtest", the
/// sub-strategies (weight, scope, config), and the backtest history.
/// Read-only. "Backtest this strategy" is the toolbar's play button.
struct StrategyDetailView: View {
    let strategyId: String

    @Environment(AppServices.self) private var services
    @State private var model: StrategyDetailModel?
    @State private var backtesting = false

    var body: some View {
        Group {
            if let model {
                if model.loading {
                    skeleton
                } else if let strategy = model.strategy {
                    detail(model, strategy)
                } else {
                    List {
                        Section {
                            Card(padding: 0) {
                                EmptyState(title: "Strategy not found", actionLabel: "Back to strategies") {
                                    services.router.go("/strategies")
                                }
                            }
                            .dsClearRow(top: 8)
                        }
                    }
                    .dsList()
                }
            } else {
                Color.clear
            }
        }
        .background(DS.Surface.page)
        .navigationTitle(model?.strategy?.name ?? "Strategy")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if model?.strategy != nil {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        backtesting = true
                    } label: {
                        Label("Backtest this strategy", systemImage: "play.fill")
                    }
                }
            }
        }
        .task(id: strategyId) {
            if model?.strategyId != strategyId {
                model = StrategyDetailModel(strategyId: strategyId, repository: { [services] in services.strategyRepository })
            }
            if let model, model.needsLoad { await model.load() }
        }
        .sheet(isPresented: $backtesting) {
            StrategyBacktestSheet(
                strategyName: model?.strategy?.name ?? "",
                linkedStrategyId: model?.strategy?.id,
                repository: { [services] in services.strategyRepository },
                onQueued: { id in services.router.push(.backtest(id)) }
            )
        }
    }

    /// The loading shape (the web's): the header lines, then card blocks.
    private var skeleton: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 12) {
                    Skeleton(width: 256, height: 14)
                    Skeleton(width: 200, height: 24)
                    Skeleton(height: 180, radius: DS.Radius.card)
                        .padding(.top, 36)
                }
                .dsClearRow(top: 4)
            }
        }
        .dsList()
        .disabled(true)
        .accessibilityLabel("Loading")
    }

    // MARK: Detail

    private func detail(_ model: StrategyDetailModel, _ strategy: Strategy) -> some View {
        let best = model.bestPnlBacktest
        return List {
            Section {
                header(model, strategy, best: best)
                    .dsClearRow(top: 0, bottom: 4)
            }

            DSSection("Sub-strategies", subtitle: "\(strategy.strategies.count) in this strategy") {
                if strategy.strategies.isEmpty {
                    EmptyState(title: "No sub-strategies defined")
                } else {
                    ForEach(Array(strategy.strategies.enumerated()), id: \.offset) { i, sub in
                        StrategySubStrategyRow(sub: sub, index: i)
                    }
                }
            }

            backtestsSection(model, best: best)
        }
        .dsList()
        .refreshable { await model.refresh() }
    }

    /// The web header: the id and counts, "Agent best" when it is the
    /// agent's pick, then the best backtest's figures and the way to it.
    private func header(_ model: StrategyDetailModel, _ strategy: Strategy, best: AgentResult?) -> some View {
        VStack(alignment: .leading, spacing: 20) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Strategy ID \(strategy.id) · \(strategy.strategies.count) sub-strategies · \(model.strategyBacktests.count) backtests")
                    .font(DS.Font.pageSub)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
                if model.isAgentBest {
                    HStack(spacing: 4) {
                        Image(systemName: "star")
                            .font(.system(size: 14))
                            .foregroundStyle(DS.Text.muted)
                        Text("Agent best")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.secondary)
                    }
                    .accessibilityElement(children: .combine)
                }
            }
            if let best {
                HStack(alignment: .bottom, spacing: 32) {
                    figure("Best P&L", fmtPnl(best.overallProfit), backtestPnlColor(best.overallProfit))
                    figure("Best P&L %", fmtPct(best.pnlPercent), backtestPnlColor(best.pnlPercent))
                    Spacer(minLength: 0)
                }
                Button("View best backtest") { services.router.push(.backtest(best.backtestId)) }
                    .buttonStyle(.dsSecondary)
            }
        }
    }

    private func figure(_ label: String, _ value: String, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(label)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
            Text(value)
                .font(DS.Font.figureLarge)
                .foregroundStyle(color)
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .accessibilityElement(children: .combine)
    }

    private static let btSortFields = [("created_at", "Date"), ("pnl", "P&L"), ("pct", "P&L %")]

    /// The backtest history, its Date / P&L / P&L % columns' sort as a
    /// header menu (choosing the active field again flips it).
    private func backtestsSection(_ model: StrategyDetailModel, best: AgentResult?) -> some View {
        DSSection("Backtests", subtitle: "\(model.strategyBacktests.count) recorded") {
            if model.strategyBacktests.isEmpty {
                EmptyState(title: "No backtests recorded for this strategy yet")
            } else {
                let bestId = best?.backtestId
                ForEach(Array(model.sortedBacktests.enumerated()), id: \.offset) { _, bt in
                    backtestRow(bt, isBest: bt.backtestId == bestId)
                }
            }
        } accessory: {
            if !model.strategyBacktests.isEmpty {
                Menu {
                    ForEach(Self.btSortFields, id: \.0) { field, label in
                        Button {
                            model.setBtSort(field)
                        } label: {
                            if model.btSortField == field {
                                Label(label, systemImage: Symbol.named(model.btSortAsc ? "arrow_upward" : "arrow_downward"))
                            } else {
                                Text(label)
                            }
                        }
                    }
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: "arrow.up.arrow.down")
                            .font(.system(size: 13))
                        Text("Sort")
                    }
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.secondary)
                    .frame(minHeight: 32)
                    .contentShape(Rectangle())
                }
                .accessibilityLabel("Sort")
            }
        }
    }

    /// One backtest (the web's row): when it ran, a star on the best one, the
    /// stocks and the period under it, and its P&L and P&L % on the right.
    private func backtestRow(_ bt: AgentResult, isBest: Bool) -> some View {
        let stocks = BacktestStocksText.stocks(bt.stocksUsed)
        let period = bt.startDate != nil ? BacktestRowFormat.dateRange(bt.startDate, bt.endDate) : nil
        return NavigationLink(value: Route.backtest(bt.backtestId)) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 8) {
                        Text(fmtDateTime(bt.createdAt))
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.primary)
                            .lineLimit(1)
                        if isBest {
                            Image(systemName: "star")
                                .font(.system(size: 14))
                                .foregroundStyle(DS.Text.secondary)
                                .accessibilityLabel("Best backtest")
                        }
                    }
                    // The web's Stocks and Period columns, a line each.
                    ForEach([stocks, period].compactMap { $0 }, id: \.self) { line in
                        Text(line)
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                            .lineLimit(1)
                    }
                }
                .monospacedDigit()
                .frame(maxWidth: .infinity, alignment: .leading)
                EntityRowValue(fmtPnl(bt.overallProfit), color: backtestPnlColor(bt.overallProfit), detail: fmtPct(bt.pnlPercent), detailColor: backtestPnlColor(bt.pnlPercent))
            }
            .accessibilityElement(children: .combine)
        }
    }
}

/// One sub-strategy (the web's card, as a row): its name and position with
/// the phase on the right, its weight and scope, then its config — the
/// fields with human labels, and the raw keys behind "Raw config".
private struct StrategySubStrategyRow: View {
    let sub: SubStrategy
    let index: Int

    @State private var rawOpen = false

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(sub.strategy)
                        .font(DS.Font.bodyMedium)
                        .foregroundStyle(DS.Text.primary)
                        .lineLimit(1)
                    Text("Position \(sub.executionPosition)")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Text(sub.decisionPhase.isEmpty ? "pre" : sub.decisionPhase)
                    .font(DS.Font.caption)
                    .foregroundStyle(StrategyDetailModel.phaseColor(sub.decisionPhase))
            }
            HStack(alignment: .top, spacing: 16) {
                field("Weight", sub.weight.map(JSON.dartDoubleString) ?? "—")
                field("Scope", sub.executionScope ?? "—")
            }
            if sub.config.isEmpty {
                Text("No config")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
            } else {
                config
            }
        }
        .padding(.vertical, 12)
    }

    private func field(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
            Text(value)
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.soft)
                .monospacedDigit()
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    /// The config (the web's "Config" list): each field's label and value,
    /// then "Raw config", tap to open, with the keys as stored.
    private var config: some View {
        VStack(alignment: .leading, spacing: 8) {
            DSDivider()
                .padding(.bottom, 8)
            Text("Config")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
            VStack(alignment: .leading, spacing: 6) {
                ForEach(sub.config.entries, id: \.key) { entry in
                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        Text(getStrategyConfigFieldMeta(sub.strategy, entry.key).label)
                            .foregroundStyle(DS.Text.secondary)
                            .lineLimit(1)
                        Spacer(minLength: 8)
                        Text(entry.value.dartDescription)
                            .foregroundStyle(DS.Text.soft)
                            .monospacedDigit()
                            .lineLimit(1)
                            .truncationMode(.tail)
                    }
                    .font(DS.Font.caption)
                    .accessibilityElement(children: .combine)
                }
            }
            disclosure("Raw config", detail: nil, open: rawOpen) { rawOpen.toggle() }
            if rawOpen {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(sub.config.entries, id: \.key) { entry in
                        Text("\(entry.key): \(entry.value.dartDescription)")
                            .font(DS.Font.mono)
                            .foregroundStyle(DS.Text.secondary)
                            .textSelection(.enabled)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(12)
                .dsCardSurface(fill: DS.Surface.page, radius: DS.Radius.control, border: DS.Line.divider)
            }
        }
    }

    private func disclosure(_ title: String, detail: String?, open: Bool, toggle: @escaping () -> Void) -> some View {
        Button {
            withAnimation(DSMotion.standard(reduceMotion: reduceMotion)) { toggle() }
        } label: {
            HStack(spacing: 8) {
                Text(title)
                    .foregroundStyle(DS.Text.label)
                if let detail {
                    Text(detail)
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                }
                Spacer(minLength: 8)
                Image(systemName: "chevron.down")
                    .font(.system(size: 12))
                    .foregroundStyle(DS.Text.muted)
                    .rotationEffect(.degrees(open ? 180 : 0))
                    .accessibilityHidden(true)
            }
            .font(DS.Font.caption)
            .frame(minHeight: 36)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(open ? .isSelected : [])
    }
}

/// "Backtest this strategy" — the web's modal, as a sheet in the trade
/// ticket's layout: the strategy under the title, the instance (an already
/// linked one, a free one, or a new one with its name), then the backtest's
/// parameters, and Cancel / Run backtest at the bottom.
struct StrategyBacktestSheet: View {
    let onQueued: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var model: StrategyBacktestFormModel
    @State private var picking: DateField?

    private enum DateField: String, Identifiable {
        case start, end
        var id: String { rawValue }
    }

    init(strategyName: String, linkedStrategyId: Int?, repository: @escaping () -> StrategyRepository, onQueued: @escaping (String) -> Void) {
        self.onQueued = onQueued
        _model = State(initialValue: StrategyBacktestFormModel(strategyName: strategyName, linkedStrategyId: linkedStrategyId, repository: repository))
    }

    var body: some View {
        NavigationStack {
            Group {
                if model.loadingInsts {
                    LoadingState(label: "Loading instances…")
                        .frame(maxHeight: .infinity)
                } else {
                    form
                }
            }
            .background(DS.Surface.card)
            .safeAreaInset(edge: .top, spacing: 0) { header }
            .safeAreaInset(edge: .bottom, spacing: 0) { footer }
            .toolbar(.hidden, for: .navigationBar)
            .navigationTitle("Backtest this strategy")
        }
        .presentationBackground(DS.Surface.card)
        .interactiveDismissDisabled(model.busy)
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
        .task { await model.loadInstances() }
        .sheet(item: $picking) { field in
            let now = Date()
            let first = Calendar.current.date(from: DateComponents(year: 2015, month: 1, day: 1))!
            let text = field == .start ? model.start : model.end
            let initial = DartDateTime.tryParse(text) ?? (field == .start ? now.addingTimeInterval(-365 * 86400) : now)
            MarketsDatePickerSheet(title: field == .start ? "Start date" : "End date", initial: initial, range: first...now) { d in
                if field == .start { model.start = KalshiFormat.ymd(d) } else { model.end = KalshiFormat.ymd(d) }
            }
        }
    }

    // MARK: Header and footer (`ui-modal-head`, `ui-modal-foot`)

    private var header: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Backtest this strategy")
                    .font(DS.Font.sectionTitle)
                    .foregroundStyle(DS.Text.primary)
                    .accessibilityAddTraits(.isHeader)
                Text(model.strategyName)
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Button { dismiss() } label: {
                Image(systemName: "xmark")
            }
            .buttonStyle(.dsIcon)
            .disabled(model.busy)
            .accessibilityLabel("Close")
        }
        .padding(.horizontal, 24)
        .padding(.top, 20)
        .padding(.bottom, 16)
        .background(DS.Surface.card)
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 12) {
            if !model.msg.isEmpty {
                Text(model.msg)
                    .font(DS.Font.body)
                    .foregroundStyle(model.busy ? DS.Text.secondary : (model.msgOk ? DS.Palette.gain : DS.Palette.loss))
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 8) {
                Spacer()
                Button("Cancel") { dismiss() }
                    .buttonStyle(.dsGhost(size: .medium))
                    .disabled(model.busy)
                Button {
                    Task {
                        if let id = await model.submit() {
                            dismiss()
                            onQueued(id)
                        }
                    }
                } label: {
                    HStack(spacing: 8) {
                        if model.busy {
                            ProgressView()
                                .controlSize(.small)
                                .tint(DS.Palette.onAccent)
                        }
                        Text("Run backtest")
                    }
                }
                .buttonStyle(.dsPrimary)
                .disabled(model.busy || model.loadingInsts)
            }
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 16)
        .background {
            DS.Surface.card
                .overlay(alignment: .top) { DSDivider() }
                .ignoresSafeArea(edges: .bottom)
        }
    }

    // MARK: Form

    private var form: some View {
        @Bindable var m = model
        return ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                DSField("Instance") {
                    VStack(alignment: .leading, spacing: 6) {
                        if !model.linkedInstances.isEmpty {
                            groupLabel("Already linked to this strategy")
                            ForEach(model.linkedInstances.indices, id: \.self) { i in
                                instanceOption(model.linkedInstances[i], linked: true)
                            }
                        }
                        if !model.freeInstances.isEmpty {
                            if !model.linkedInstances.isEmpty {
                                groupLabel("Available instances (no strategy)")
                                    .padding(.top, 6)
                            }
                            ForEach(model.freeInstances.indices, id: \.self) { i in
                                instanceOption(model.freeInstances[i], linked: false)
                            }
                        }
                        option(selected: model.selectedInstId.isEmpty, action: { model.selectedInstId = "" }) {
                            Text("Create new instance")
                                .font(DS.Font.body)
                                .foregroundStyle(DS.Text.soft)
                        }
                        .padding(.top, model.linkedInstances.isEmpty && model.freeInstances.isEmpty ? 0 : 6)
                    }
                }
                if model.selectedInstId.isEmpty {
                    DSField("New instance name") {
                        DSTextField("e.g. My strategy test", text: $m.newInstName)
                    }
                }
                DSDivider()
                VStack(alignment: .leading, spacing: 16) {
                    Text("Backtest parameters")
                        .font(DS.Font.bodyMedium)
                        .foregroundStyle(DS.Text.primary)
                    DSField("Stocks (comma-separated)") {
                        DSTextField("AAPL, MSFT, NVDA", text: $m.stocks)
                            .textInputAutocapitalization(.characters)
                            .autocorrectionDisabled()
                    }
                    HStack(alignment: .top, spacing: 12) {
                        DSField("Start date") { dateButton(model.start) { picking = .start } }
                        DSField("End date") { dateButton(model.end) { picking = .end } }
                    }
                    HStack(alignment: .top, spacing: 12) {
                        DSField("Granularity") {
                            Menu {
                                Picker("Granularity", selection: $m.granularity) {
                                    ForEach(StrategyBacktestFormModel.granularities, id: \.value) { g in Text(g.label).tag(g.value) }
                                }
                            } label: {
                                selectLabel(StrategyBacktestFormModel.granularities.first { $0.value == model.granularity }?.label ?? model.granularity)
                            }
                            .accessibilityLabel("Granularity")
                        }
                        DSField("Initial cash ($)") {
                            DSTextField("10000", text: $m.cash)
                                .keyboardType(.numberPad)
                                .monospacedDigit()
                        }
                    }
                }
            }
            .padding(.horizontal, 24)
            .padding(.bottom, 24)
        }
        .scrollDismissesKeyboard(.interactively)
    }

    private func groupLabel(_ text: String) -> some View {
        Text(text)
            .font(DS.Font.caption)
            .foregroundStyle(DS.Text.muted)
            .padding(.bottom, 2)
    }

    /// A selectable option (the web's radio rows): bordered, raised with a
    /// brighter hairline when chosen, a radio mark at the leading edge.
    private func option<Content: View>(selected: Bool, action: @escaping () -> Void, @ViewBuilder content: () -> Content) -> some View {
        Button(action: action) {
            HStack(spacing: 12) {
                ZStack {
                    Circle()
                        .strokeBorder(selected ? DS.Palette.accent : DS.Line.focus, lineWidth: 1)
                    if selected {
                        Circle()
                            .fill(DS.Palette.accent)
                            .padding(4)
                    }
                }
                .frame(width: 16, height: 16)
                .accessibilityHidden(true)
                content()
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .frame(minHeight: 48)
            .contentShape(Rectangle())
            .dsCardSurface(
                fill: selected ? DS.Surface.raised : .clear,
                radius: DS.Radius.control,
                border: selected ? Color.white.opacity(0.2) : DS.Line.card
            )
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func instanceOption(_ inst: JSONObject, linked: Bool) -> some View {
        let id = StrategyBacktestFormModel.instanceId(inst)
        return option(selected: model.selectedInstId == id, action: { model.selectedInstId = id }) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(StrategyBacktestFormModel.instanceName(inst))
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.primary)
                        .lineLimit(1)
                    Text(id)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if linked {
                    Text("Linked")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.secondary)
                }
            }
        }
    }

    /// A date as the web's date input: the value, or the zinc-600 hint.
    private func dateButton(_ value: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(value.isEmpty ? "YYYY-MM-DD" : value)
                .foregroundStyle(value.isEmpty ? DS.Text.faint : DS.Text.soft)
                .monospacedDigit()
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
                .dsInput()
        }
        .buttonStyle(.plain)
    }

    /// A select as the web draws it: the value and a chevron in an input.
    private func selectLabel(_ text: String) -> some View {
        HStack(spacing: 8) {
            Text(text)
                .foregroundStyle(DS.Text.soft)
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
            Image(systemName: "chevron.up.chevron.down")
                .font(.system(size: 12))
                .foregroundStyle(DS.Text.muted)
        }
        .contentShape(Rectangle())
        .dsInput()
    }
}
