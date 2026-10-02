import SwiftUI

/// One strategy (`/strategies/:id`) — `StrategyDetailScreen`: the overview
/// with the best backtest, the sub-strategy composition and the backtest
/// history. Read-only, as in Flutter. An inset-grouped list under the inline
/// strategy name; "Backtest This Strategy" is the toolbar's play button.
struct StrategyDetailView: View {
    let strategyId: String

    @Environment(AppServices.self) private var services
    @State private var model: StrategyDetailModel?
    @State private var backtesting = false

    var body: some View {
        Group {
            if let model {
                if model.loading {
                    ProgressView()
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let strategy = model.strategy {
                    detail(model, strategy)
                } else {
                    ContentUnavailableView {
                        Label("Strategy not found", systemImage: Symbol.named("schema"))
                    } actions: {
                        Button("Back to Strategies") { services.router.go("/strategies") }
                    }
                }
            } else {
                Color.clear
            }
        }
        .background(DS.Surface.canvas)
        .navigationTitle(model?.strategy?.name ?? "Strategy")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if model?.strategy != nil {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        backtesting = true
                    } label: {
                        Label("Backtest This Strategy", systemImage: "play.fill")
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

    // MARK: Detail

    private func detail(_ model: StrategyDetailModel, _ strategy: Strategy) -> some View {
        let best = model.bestPnlBacktest
        return List {
            Section {
                StatGrid(columns: 3) {
                    StatCell(label: "Best P&L", value: best.map { fmtPnl($0.overallProfit) } ?? "—", valueColor: best.map { pnlColor($0.overallProfit) })
                    StatCell(label: "Best P&L%", value: best.map { fmtPct($0.pnlPercent) } ?? "—", valueColor: best.map { pnlColor($0.pnlPercent) })
                    StatCell(label: "Backtests", value: "\(model.strategyBacktests.count)")
                    StatCell(label: "Strategy ID", value: "\(strategy.id)")
                    StatCell(label: "Sub-strategies", value: "\(strategy.strategies.count)")
                }
                .padding(.vertical, 4)
                if let best {
                    NavigationLink(value: Route.backtest(best.backtestId)) {
                        Label("Best Backtest", systemImage: Symbol.named("analytics"))
                    }
                }
            } header: {
                DSSectionHeader("Overview") {
                    if model.isAgentBest {
                        MarketsTag(text: "Agent best", color: DS.Palette.warning)
                    }
                }
            }

            Section("Sub-strategies (\(strategy.strategies.count))") {
                if strategy.strategies.isEmpty {
                    Text("No sub-strategies defined.").foregroundStyle(.secondary)
                } else {
                    ForEach(Array(strategy.strategies.enumerated()), id: \.offset) { _, sub in
                        StrategySubStrategyRow(sub: sub)
                    }
                }
            }

            backtestsSection(model, best: best)
        }
        .listStyle(.insetGrouped)
        .refreshable { await model.refresh() }
    }

    private static let btSortFields = [("created_at", "Date"), ("pnl", "P&L"), ("pct", "P&L%")]

    /// The backtest history, its Date / P&L / P&L% sort chips as a header
    /// menu (choosing the active field again flips it).
    private func backtestsSection(_ model: StrategyDetailModel, best: AgentResult?) -> some View {
        Section {
            if model.strategyBacktests.isEmpty {
                Text("No backtests yet.").foregroundStyle(.secondary)
            } else {
                let bestId = best?.backtestId
                ForEach(Array(model.sortedBacktests.enumerated()), id: \.offset) { _, bt in
                    backtestRow(bt, isBest: bt.backtestId == bestId)
                }
            }
        } header: {
            DSSectionHeader("Backtests (\(model.strategyBacktests.count))") {
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
                        Label("Sort", systemImage: "arrow.up.arrow.down")
                            .labelStyle(.titleAndIcon)
                            .font(.footnote)
                    }
                }
            }
        } footer: {
            if model.strategyBacktests.isEmpty {
                Text("Run a backtest to see results here.")
            }
        }
    }

    private func backtestRow(_ bt: AgentResult, isBest: Bool) -> some View {
        var parts: [String] = []
        if !bt.stocksUsed.isEmpty {
            parts.append(bt.stocksUsed.prefix(4).joined(separator: ", ") + (bt.stocksUsed.count > 4 ? " +\(bt.stocksUsed.count - 4)" : ""))
        }
        if bt.startDate != nil {
            parts.append("\(fmtDate(bt.startDate)) – \(fmtDate(bt.endDate))")
        }
        return NavigationLink(value: Route.backtest(bt.backtestId)) {
            EntityRow(fmtDateTime(bt.createdAt), subtitle: parts.isEmpty ? nil : parts.joined(separator: " · "), subtitleLineLimit: 2) {
                Image(systemName: Symbol.named("auto_awesome"))
                    .font(.footnote)
                    .foregroundStyle(DS.Palette.warning)
                    .opacity(isBest ? 1 : 0)
                    .accessibilityLabel(isBest ? "Best backtest" : "")
                    .accessibilityHidden(!isBest)
            } trailing: {
                EntityRowValue(fmtPnl(bt.overallProfit), color: pnlColor(bt.overallProfit), detail: fmtPct(bt.pnlPercent), detailColor: pnlColor(bt.pnlPercent))
            }
        }
    }
}

/// One sub-strategy: its name, position, weight and scope over the phase;
/// expand it for the config as `LabeledContent` rows with human labels, and
/// the raw keys in a monospaced "Raw config" disclosure.
private struct StrategySubStrategyRow: View {
    let sub: SubStrategy

    @State private var open = false
    @State private var rawOpen = false

    var body: some View {
        DisclosureGroup(isExpanded: $open) {
            if sub.config.isEmpty {
                Text("No config.").foregroundStyle(.secondary)
            } else {
                ForEach(sub.config.entries, id: \.key) { entry in
                    LabeledContent(getStrategyConfigFieldMeta(sub.strategy, entry.key).label) {
                        Text(entry.value.dartDescription)
                            .lineLimit(1)
                            .truncationMode(.tail)
                    }
                }
                DisclosureGroup("Raw config", isExpanded: $rawOpen) {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(sub.config.entries, id: \.key) { entry in
                            Text("\(entry.key): \(entry.value.dartDescription)")
                                .font(.caption.monospaced())
                                .foregroundStyle(.secondary)
                                .textSelection(.enabled)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        } label: {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(sub.strategy).font(.headline).lineLimit(1)
                    Text("position \(sub.executionPosition) · Weight \(sub.weight.map(JSON.dartDoubleString) ?? "—") · Scope \(sub.executionScope ?? "—")")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                StatusBadge(label: sub.decisionPhase.dsSentenceCased, color: StrategyDetailModel.phaseColor(sub.decisionPhase))
            }
        }
    }
}

/// "Backtest this strategy" — `_BacktestModal`: choose a linked, free or new
/// instance, set the parameters, and queue the run. A native form; Run
/// Backtest is the toolbar's confirm action.
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
                    LoadingState(label: "Loading instances...").frame(maxHeight: .infinity)
                } else {
                    form
                }
            }
            .navigationTitle("Backtest This Strategy")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }.disabled(model.busy)
                }
                ToolbarItem(placement: .confirmationAction) { runButton }
            }
        }
        .interactiveDismissDisabled(model.busy)
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
        .task { await model.loadInstances() }
        .sheet(item: $picking) { field in
            let now = Date()
            let first = Calendar.current.date(from: DateComponents(year: 2015, month: 1, day: 1))!
            let text = field == .start ? model.start : model.end
            let initial = DartDateTime.tryParse(text) ?? (field == .start ? now.addingTimeInterval(-365 * 86400) : now)
            MarketsDatePickerSheet(title: field == .start ? "Start Date" : "End Date", initial: initial, range: first...now) { d in
                if field == .start { model.start = KalshiFormat.ymd(d) } else { model.end = KalshiFormat.ymd(d) }
            }
        }
    }

    private var form: some View {
        @Bindable var m = model
        return Form {
            Section {
                LabeledContent("Strategy", value: model.strategyName)
            }
            Section("Select instance") {
                if !model.linkedInstances.isEmpty {
                    Text("Already linked to this strategy").font(.footnote.weight(.semibold)).foregroundStyle(.secondary)
                    ForEach(model.linkedInstances.indices, id: \.self) { i in
                        instanceRow(model.linkedInstances[i], linked: true)
                    }
                }
                if !model.freeInstances.isEmpty {
                    Text("Available instances (no strategy)").font(.footnote.weight(.semibold)).foregroundStyle(.secondary)
                    ForEach(model.freeInstances.indices, id: \.self) { i in
                        instanceRow(model.freeInstances[i], linked: false)
                    }
                }
                Button {
                    model.selectedInstId = ""
                } label: {
                    HStack {
                        Label("Create new instance", systemImage: Symbol.named("add_circle")).foregroundStyle(.primary)
                        Spacer()
                        check(selected: model.selectedInstId.isEmpty)
                    }
                }
                .tint(.primary)
                .accessibilityAddTraits(model.selectedInstId.isEmpty ? .isSelected : [])
                if model.selectedInstId.isEmpty {
                    TextField("New instance name", text: $m.newInstName, prompt: Text("e.g. My Strategy Test"))
                }
            }
            Section("Backtest parameters") {
                LabeledContent("Stocks (comma-separated)") {
                    TextField("Stocks (comma-separated)", text: $m.stocks, prompt: Text("AAPL, MSFT, NVDA"))
                        .multilineTextAlignment(.trailing)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                }
                dateRow("Start date", model.start) { picking = .start }
                dateRow("End date", model.end) { picking = .end }
                Picker("Granularity", selection: $m.granularity) {
                    ForEach(StrategyBacktestFormModel.granularities, id: \.value) { g in Text(g.label).tag(g.value) }
                }
                LabeledContent("Initial cash ($)") {
                    TextField("10000", text: $m.cash)
                        .keyboardType(.numberPad)
                        .multilineTextAlignment(.trailing)
                        .monospacedDigit()
                }
            }
            if !model.msg.isEmpty {
                let color = model.msgOk ? DS.Palette.success : DS.Palette.danger
                Section {
                    Label(model.msg, systemImage: Symbol.named(model.msgOk ? "check_circle" : "error"))
                        .font(.footnote)
                        .foregroundStyle(color)
                }
            }
        }
    }

    private func dateRow(_ label: String, _ value: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            LabeledContent(label) {
                Text(value.isEmpty ? "YYYY-MM-DD" : value)
                    .monospacedDigit()
                    .foregroundStyle(value.isEmpty ? .tertiary : .secondary)
            }
        }
        .foregroundStyle(.primary)
    }

    private func instanceRow(_ inst: JSONObject, linked: Bool) -> some View {
        let id = StrategyBacktestFormModel.instanceId(inst)
        let selected = model.selectedInstId == id
        return Button {
            model.selectedInstId = id
        } label: {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(StrategyBacktestFormModel.instanceName(inst))
                        .foregroundStyle(.primary)
                    Text(id)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Spacer()
                if linked { MarketsTag(text: "Linked", color: DS.Palette.success) }
                check(selected: selected)
            }
        }
        .tint(.primary)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    /// The selected row's checkmark (the Dart radio button).
    private func check(selected: Bool) -> some View {
        Image(systemName: "checkmark")
            .font(.body.weight(.semibold))
            .foregroundStyle(DS.Palette.accent)
            .opacity(selected ? 1 : 0)
            .accessibilityHidden(true)
    }

    /// The Dart footer button, in the toolbar: the same label, spinner and
    /// in-flight guard.
    private var runButton: some View {
        Button {
            Task {
                if let id = await model.submit() {
                    dismiss()
                    onQueued(id)
                }
            }
        } label: {
            if model.busy {
                ProgressView()
            } else {
                Label("Run Backtest", systemImage: "checkmark")
            }
        }
        .dsGlassProminentButton()
        .disabled(model.busy)
    }
}
