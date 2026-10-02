import SwiftUI

/// One strategy (`/strategies/:id`) — `StrategyDetailScreen`: header with
/// the best backtest, the sub-strategy composition and the backtest history.
/// Read-only, as in Flutter.
struct StrategyDetailView: View {
    let strategyId: String

    @Environment(AppServices.self) private var services
    @Environment(\.colorScheme) private var colorScheme
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
                        Label("Backtest This Strategy", systemImage: Symbol.named("play_circle"))
                    }
                    .tint(DS.Palette.info)
                }
            }
        }
        .task(id: strategyId) {
            if model?.strategyId != strategyId {
                let m = StrategyDetailModel(strategyId: strategyId, repository: { [services] in services.strategyRepository })
                model = m
                await m.load()
            }
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
        let isAgentBest = model.isAgentBest
        let amber = DS.Palette.warning
        return ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Card(padding: 18) {
                    VStack(alignment: .leading, spacing: 14) {
                        HStack(alignment: .top, spacing: 12) {
                            Image(systemName: Symbol.named(isAgentBest ? "auto_awesome" : "schema"))
                                .font(.title3)
                                .foregroundStyle(isAgentBest ? amber : Color.secondary)
                                .frame(width: 48, height: 48)
                                .background(isAgentBest ? amber.opacity(DS.tintFill) : DS.Surface.inset, in: .rect(cornerRadius: 14, style: .continuous))
                            VStack(alignment: .leading, spacing: 4) {
                                HStack(spacing: 8) {
                                    Text(strategy.name)
                                        .font(.title3.bold())
                                        .foregroundStyle(isAgentBest ? DS.Palette.onTint(amber, in: colorScheme) : Color.primary)
                                    if isAgentBest { MarketsTag(text: "AGENT BEST", color: amber) }
                                }
                                Text("Strategy ID \(strategy.id) · \(strategy.strategies.count) sub-strategies · \(model.strategyBacktests.count) backtests")
                                    .font(.footnote)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        if let best {
                            HStack(alignment: .bottom, spacing: 20) {
                                headerStat("BEST P&L", fmtPnl(best.overallProfit), pnlColor(best.overallProfit))
                                headerStat("BEST P&L%", fmtPct(best.pnlPercent), pnlColor(best.pnlPercent))
                                Spacer(minLength: 0)
                                Button {
                                    services.router.push(.backtest(best.backtestId))
                                } label: {
                                    Label("Best Backtest", systemImage: Symbol.named("analytics")).font(.caption.weight(.semibold))
                                }
                                .buttonStyle(.bordered)
                                .controlSize(.small)
                                .tint(DS.Palette.accent)
                            }
                        }
                    }
                }
                .padding(.bottom, 8)

                SectionHeader(title: "Sub-strategies (\(strategy.strategies.count))", eyebrow: "COMPOSITION")
                if strategy.strategies.isEmpty {
                    Card {
                        Text("No sub-strategies defined.")
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity)
                    }
                } else {
                    ForEach(Array(strategy.strategies.enumerated()), id: \.offset) { _, sub in subStrategyCard(sub) }
                }

                SectionHeader(title: "Backtests (\(model.strategyBacktests.count))", eyebrow: "HISTORY")
                    .padding(.top, 8)
                if !model.strategyBacktests.isEmpty {
                    btSortBar(model)
                }
                if model.strategyBacktests.isEmpty {
                    EmptyState(
                        systemImage: Symbol.named("analytics"),
                        title: "No backtests yet.",
                        subtitle: "Run a backtest to see results here."
                    )
                } else {
                    let bestId = best?.backtestId
                    ForEach(Array(model.sortedBacktests.enumerated()), id: \.offset) { _, bt in
                        backtestRow(bt, isBest: bt.backtestId == bestId)
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 32)
        }
        .refreshable { await model.refresh() }
    }

    private func headerStat(_ label: String, _ value: String, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption2).tracking(0.8).foregroundStyle(.tertiary)
            Text(value).font(.title3.bold().monospacedDigit()).foregroundStyle(color)
        }
    }

    private func subStrategyCard(_ sub: SubStrategy) -> some View {
        let phase = StrategyDetailModel.phaseColor(sub.decisionPhase)
        return Card(padding: 14) {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(sub.strategy).font(.subheadline.monospaced().weight(.semibold))
                        Text("position \(sub.executionPosition)").font(.caption2).foregroundStyle(.tertiary)
                    }
                    Spacer()
                    MarketsTag(text: sub.decisionPhase.uppercased(), color: phase)
                }
                HStack(spacing: 12) {
                    metaChip("Weight", sub.weight.map(JSON.dartDoubleString) ?? "—")
                    metaChip("Scope", sub.executionScope ?? "—")
                }
                if sub.config.isEmpty {
                    Text("No config.").font(.caption2).foregroundStyle(.tertiary)
                } else {
                    Divider()
                    Text("CONFIG").font(.caption2).tracking(1).foregroundStyle(.tertiary)
                    ForEach(sub.config.entries, id: \.key) { entry in
                        HStack(alignment: .top, spacing: 8) {
                            Text(getStrategyConfigFieldMeta(sub.strategy, entry.key).label)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .layoutPriority(5)
                            Text(entry.value.dartDescription)
                                .font(.caption.monospaced().weight(.medium))
                                .lineLimit(1)
                                .truncationMode(.tail)
                                .frame(maxWidth: .infinity, alignment: .trailing)
                                .layoutPriority(3)
                        }
                    }
                }
            }
        }
    }

    private func metaChip(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption2).foregroundStyle(.tertiary)
            Text(value).font(.footnote.monospaced()).foregroundStyle(.secondary)
        }
    }

    private func btSortBar(_ model: StrategyDetailModel) -> some View {
        HStack(spacing: 6) {
            Text("Sort:").font(.caption2).foregroundStyle(.tertiary)
            ForEach([("created_at", "Date"), ("pnl", "P&L"), ("pct", "P&L%")], id: \.0) { field, label in
                let active = model.btSortField == field
                Button {
                    model.setBtSort(field)
                } label: {
                    HStack(spacing: 3) {
                        Text(label)
                        if active {
                            Image(systemName: Symbol.named(model.btSortAsc ? "arrow_upward" : "arrow_downward")).font(.caption2)
                        }
                    }
                    .font(.caption.weight(active ? .semibold : .regular))
                    .foregroundStyle(active ? AnyShapeStyle(.tint) : AnyShapeStyle(.secondary))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(active ? DS.Palette.accent.opacity(DS.tintFill) : DS.Surface.panel, in: Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(active ? .isSelected : [])
            }
        }
    }

    private func backtestRow(_ bt: AgentResult, isBest: Bool) -> some View {
        Button {
            services.router.push(.backtest(bt.backtestId))
        } label: {
            Card(padding: EdgeInsets(top: 10, leading: 14, bottom: 10, trailing: 14)) {
                HStack(spacing: 8) {
                    if isBest {
                        Image(systemName: Symbol.named("auto_awesome")).font(.caption).foregroundStyle(DS.Palette.warning)
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        Text(fmtDateTime(bt.createdAt)).font(.footnote).foregroundStyle(.primary)
                        HStack(spacing: 8) {
                            if !bt.stocksUsed.isEmpty {
                                Text(bt.stocksUsed.prefix(4).joined(separator: ", ") + (bt.stocksUsed.count > 4 ? " +\(bt.stocksUsed.count - 4)" : ""))
                                    .font(.caption.monospaced())
                                    .foregroundStyle(.secondary)
                            }
                            if bt.startDate != nil {
                                Text("\(fmtDate(bt.startDate)) – \(fmtDate(bt.endDate))")
                                    .font(.caption2)
                                    .foregroundStyle(.tertiary)
                            }
                        }
                    }
                    Spacer(minLength: 8)
                    VStack(alignment: .trailing, spacing: 0) {
                        Text(fmtPnl(bt.overallProfit)).font(.footnote.monospaced().weight(.semibold)).foregroundStyle(pnlColor(bt.overallProfit))
                        Text(fmtPct(bt.pnlPercent)).font(.caption.monospaced().weight(.semibold)).foregroundStyle(pnlColor(bt.pnlPercent))
                    }
                    Image(systemName: Symbol.named("open_in_new")).font(.caption).foregroundStyle(.tertiary)
                }
            }
        }
        .buttonStyle(.plain)
    }

    private var skeleton: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Skeleton(height: 120, radius: DS.Radius.card)
                Skeleton.line(width: 160, height: 12)
                ForEach(0..<3, id: \.self) { _ in Skeleton(height: 110, radius: DS.Radius.card) }
                Skeleton.line(width: 130, height: 12)
                ForEach(0..<4, id: \.self) { _ in Skeleton(height: 54, radius: DS.Radius.card) }
            }
            .padding(16)
        }
        .accessibilityLabel("Loading")
    }
}

/// "Backtest this strategy" — `_BacktestModal`: choose a linked, free or new
/// instance, set the parameters, and queue the run.
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
        @Bindable var m = model
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
            }
            .safeAreaInset(edge: .bottom) { footer }
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
                Text(model.strategyName).font(.footnote).foregroundStyle(.secondary)
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets())
            }
            Section("SELECT INSTANCE") {
                if !model.linkedInstances.isEmpty {
                    Text("Already linked to this strategy").font(.caption.weight(.semibold)).foregroundStyle(DS.Palette.success)
                    ForEach(model.linkedInstances.indices, id: \.self) { i in
                        instanceRow(model.linkedInstances[i], linked: true)
                    }
                }
                if !model.freeInstances.isEmpty {
                    Text("Available instances (no strategy)").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
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
                        radio(selected: model.selectedInstId.isEmpty, color: DS.Palette.accent)
                    }
                }
                .accessibilityAddTraits(model.selectedInstId.isEmpty ? .isSelected : [])
                if model.selectedInstId.isEmpty {
                    labelled("NEW INSTANCE NAME") {
                        TextField("NEW INSTANCE NAME", text: $m.newInstName, prompt: Text("e.g. My Strategy Test"))
                    }
                }
            }
            Section("BACKTEST PARAMETERS") {
                labelled("STOCKS (comma-separated)") {
                    TextField("STOCKS (comma-separated)", text: $m.stocks, prompt: Text("AAPL, MSFT, NVDA"))
                        .font(.body.monospaced())
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                }
                dateRow("START DATE", model.start) { picking = .start }
                dateRow("END DATE", model.end) { picking = .end }
                Picker("GRANULARITY", selection: $m.granularity) {
                    ForEach(StrategyBacktestFormModel.granularities, id: \.value) { g in Text(g.label).tag(g.value) }
                }
                LabeledContent("INITIAL CASH ($)") {
                    TextField("10000", text: $m.cash)
                        .keyboardType(.numberPad)
                        .multilineTextAlignment(.trailing)
                        .font(.body.monospaced())
                }
            }
        }
    }

    private func labelled<F: View>(_ label: String, @ViewBuilder field: () -> F) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label).font(.caption2).tracking(0.8).foregroundStyle(.secondary)
            field()
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
        let accent = linked ? DS.Palette.success : DS.Palette.accent
        return Button {
            model.selectedInstId = id
        } label: {
            HStack(spacing: 10) {
                Image(systemName: Symbol.named("schema")).foregroundStyle(.tertiary)
                VStack(alignment: .leading, spacing: 0) {
                    Text(StrategyBacktestFormModel.instanceName(inst))
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(linked ? DS.Palette.success : Color.primary)
                    Text(id).font(.caption2.monospaced()).foregroundStyle(.tertiary)
                }
                Spacer()
                if linked { MarketsTag(text: "LINKED", color: DS.Palette.success) }
                radio(selected: selected, color: accent)
            }
        }
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func radio(selected: Bool, color: Color) -> some View {
        Image(systemName: selected ? "largecircle.fill.circle" : "circle")
            .foregroundStyle(selected ? color : Color.secondary)
            .accessibilityHidden(true)
    }

    private var footer: some View {
        VStack(spacing: 12) {
            if !model.msg.isEmpty {
                let color = model.msgOk ? DS.Palette.success : DS.Palette.danger
                HStack(spacing: 8) {
                    Image(systemName: Symbol.named(model.msgOk ? "check_circle" : "error"))
                    Text(model.msg).frame(maxWidth: .infinity, alignment: .leading)
                }
                .font(.footnote)
                .foregroundStyle(color)
                .padding(10)
                .background(color.opacity(0.1), in: .rect(cornerRadius: 8, style: .continuous))
            }
            Button {
                Task {
                    if let id = await model.submit() {
                        dismiss()
                        onQueued(id)
                    }
                }
            } label: {
                HStack {
                    if model.busy { ProgressView() }
                    Label("Run Backtest", systemImage: Symbol.named("play_circle")).fontWeight(.semibold)
                }
                .frame(maxWidth: .infinity)
            }
            .dsProminentButton()
            .controlSize(.large)
            .disabled(model.busy)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 10)
        .background(.bar)
    }
}
